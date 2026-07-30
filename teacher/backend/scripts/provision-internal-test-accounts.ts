import { randomUUID } from 'node:crypto';
import { Pool, type PoolClient, type QueryResultRow } from 'pg';
import { PasswordHasher } from '../src/auth/password-hasher';

const TEST_TEACHERS = [
  { teacherId: '7049494', email: 'tide.day15@example.invalid' },
  { teacherId: '7041218', email: 'tide.score29@example.invalid' },
  { teacherId: '7091018', email: 'tide.score73@example.invalid' },
  { teacherId: '7104753', email: 'tide.score181@example.invalid' },
  { teacherId: '6959310', email: 'tide.day30@example.invalid' },
  { teacherId: '4998291', email: 'tide.4998291@example.invalid' },
  { teacherId: '5008955', email: 'tide.5008955@example.invalid' },
  { teacherId: '5043173', email: 'tide.5043173@example.invalid' },
  { teacherId: '5212002', email: 'tide.5212002@example.invalid' },
] as const;

interface SourceTeacherRow extends QueryResultRow {
  teacherId: string;
  campEnrollmentId: string;
  name: string;
  country: string | null;
  timezone: string;
  campDay: number;
  graduationState: string;
  totalScore: number;
  graduationThreshold: number;
  dataMode: 'MIXED' | 'REAL' | 'MOCK';
  sourceBatchId: string | null;
  sourceSnapshotLabel: string | null;
  sourceUpdatedAt: Date;
  tesolCompleted: boolean | null;
  selfIntroduced: boolean | null;
}

function requiredEnvironment(name: string): string {
  const value = process.env[name];
  if (!value) throw new Error(`${name} is required`);
  return value;
}

async function queryTeacher(
  database: Pool,
  teacherId: string,
): Promise<SourceTeacherRow | null> {
  const result = await database.query<SourceTeacherRow>(
    `
      SELECT
        teacher.teacher_id AS "teacherId",
        teacher.camp_enrollment_id AS "campEnrollmentId",
        teacher.name,
        teacher.country,
        COALESCE(teacher.timezone, 'Asia/Shanghai') AS timezone,
        teacher.camp_day AS "campDay",
        teacher.graduation_state AS "graduationState",
        COALESCE(teacher.total_score, 0) AS "totalScore",
        COALESCE(teacher.graduation_threshold, 100) AS "graduationThreshold",
        teacher.data_mode AS "dataMode",
        teacher.source_batch_id AS "sourceBatchId",
        teacher.source_snapshot_label AS "sourceSnapshotLabel",
        COALESCE(metric.updated_at, teacher.updated_at) AS "sourceUpdatedAt",
        metric.is_cpl_tesol AS "tesolCompleted",
        metric.is_self_introduce AS "selfIntroduced"
      FROM public.teachers teacher
      LEFT JOIN LATERAL (
        SELECT snapshot.*
        FROM public.teacher_metric_snapshots snapshot
        WHERE snapshot.teacher_id = teacher.teacher_id
        ORDER BY snapshot.updated_at DESC, snapshot.created_at DESC, snapshot.snapshot_id DESC
        LIMIT 1
      ) metric ON true
      WHERE teacher.teacher_id = $1
      LIMIT 1
    `,
    [teacherId],
  );
  return result.rows[0] ?? null;
}

async function provisionAccount(
  client: PoolClient,
  teacher: SourceTeacherRow,
  email: string,
  passwordHash: string,
): Promise<void> {
  const account = await client.query<{ id: string }>(
    `
      INSERT INTO tide.user_accounts (
        id, email, normalized_email, password_hash, status, email_verified_at
      ) VALUES ($1, $2, $2, $3, 'ACTIVE', now())
      ON CONFLICT (normalized_email) DO UPDATE SET
        email = EXCLUDED.email,
        password_hash = EXCLUDED.password_hash,
        status = 'ACTIVE',
        email_verified_at = COALESCE(tide.user_accounts.email_verified_at, now()),
        updated_at = now()
      RETURNING id
    `,
    [randomUUID(), email, passwordHash],
  );
  const accountId = account.rows[0].id;

  const existingBinding = await client.query<{ accountId: string }>(
    `
      SELECT account_id AS "accountId"
      FROM tide.teacher_bindings
      WHERE teacher_id = $1
      LIMIT 1
    `,
    [teacher.teacherId],
  );
  if (
    existingBinding.rows[0] &&
    existingBinding.rows[0].accountId !== accountId
  ) {
    throw new Error(
      `Teacher already belongs to another account: ${teacher.teacherId}`,
    );
  }

  const binding = await client.query<{ id: string }>(
    `
      INSERT INTO tide.teacher_bindings (
        id, account_id, teacher_id, source_system, status
      ) VALUES ($1, $2, $3, 'SHIWEN', 'ACTIVE')
      ON CONFLICT (teacher_id) DO UPDATE SET
        status = 'ACTIVE', ended_at = NULL, updated_at = now()
      RETURNING id
    `,
    [randomUUID(), accountId, teacher.teacherId],
  );

  await client.query(
    `
      INSERT INTO tide.binding_audit_events (
        id, binding_id, account_id, event_type, new_teacher_id,
        actor_type, actor_ref, reason
      )
      SELECT $1, $2, $3, 'BOUND', $4, 'ADMIN',
        'INTERNAL_TEST_PROVISIONER', 'INTERNAL_TEST_ACCOUNT'
      WHERE NOT EXISTS (
        SELECT 1 FROM tide.binding_audit_events
        WHERE new_teacher_id = $4
          AND actor_ref = 'INTERNAL_TEST_PROVISIONER'
      )
    `,
    [randomUUID(), binding.rows[0].id, accountId, teacher.teacherId],
  );

}

async function main(): Promise<void> {
  const database = new Pool({
    connectionString: requiredEnvironment('TIDE_DATABASE_URL'),
  });
  const passwordHash = await new PasswordHasher().hash(
    requiredEnvironment('INTERNAL_TEST_PASSWORD'),
  );
  const prepared: Array<Record<string, unknown>> = [];

  try {
    for (const selection of TEST_TEACHERS) {
      const teacher = await queryTeacher(database, selection.teacherId);
      if (!teacher) {
        prepared.push({
          email: selection.email,
          teacherId: selection.teacherId,
          skipped: true,
          reason: 'TEACHER_NOT_FOUND_IN_COMPANY_TEST',
        });
        continue;
      }

      const client = await database.connect();
      try {
        await client.query('BEGIN');
        await provisionAccount(client, teacher, selection.email, passwordHash);
        await client.query('COMMIT');
      } catch (error) {
        await client.query('ROLLBACK');
        throw error;
      } finally {
        client.release();
      }
      prepared.push({
        email: selection.email,
        teacherId: teacher.teacherId,
        name: teacher.name,
        campDay: teacher.campDay,
        dataMode: teacher.dataMode,
      });
    }
  } finally {
    await database.end();
  }

  process.stdout.write(`${JSON.stringify({ prepared }, null, 2)}\n`);
}

void main().catch((error: unknown) => {
  process.stderr.write(
    `${error instanceof Error ? error.message : String(error)}\n`,
  );
  process.exitCode = 1;
});
