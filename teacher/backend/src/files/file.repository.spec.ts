import type { PoolClient } from 'pg';
import type { DatabaseService } from '../platform/database/database.service';
import { FileRepository } from './file.repository';

const expiresAt = new Date('2026-08-11T10:15:00.000Z');
const input = {
  fileId: 'file-001',
  accountId: 'account-001',
  taskAssignmentId: 'assignment-001',
  stepKey: 'p-fb-negative-environment-photo',
  idempotencyKey: 'upload-key-001',
  requestHash: 'request-hash-001',
  storageProvider: 'LOCAL' as const,
  objectKey: 'uploads/account-001/file-001',
  originalFilename: 'environment.jpg',
  mimeType: 'image/jpeg',
  sizeBytes: 1_024,
  sha256: 'a'.repeat(64),
  expiresAt,
};

interface TaskStepFixture {
  taskCode: string;
  contentConfig: Record<string, unknown>;
  evidenceSnapshot: unknown;
}

function createRepository(taskStep: TaskStepFixture) {
  const query = jest.fn((sql: string) => {
    if (sql.includes('pg_advisory_xact_lock')) {
      return { rows: [], rowCount: 1 };
    }
    if (sql.includes('FROM tide.file_upload_intents intent')) {
      return { rows: [], rowCount: 0 };
    }
    if (sql.includes('execution.task_code AS "taskCode"')) {
      return { rows: [taskStep], rowCount: 1 };
    }
    if (sql.includes('INSERT INTO tide.file_objects')) {
      return { rows: [], rowCount: 1 };
    }
    if (sql.includes('INSERT INTO tide.file_upload_intents')) {
      return {
        rows: [
          {
            fileId: input.fileId,
            status: 'PENDING',
            mimeType: input.mimeType,
            sizeBytes: String(input.sizeBytes),
            sha256: input.sha256,
            storageProvider: input.storageProvider,
            objectKey: input.objectKey,
            originalFilename: input.originalFilename,
            expiresAt,
            uploadReceivedAt: null,
            requestHash: input.requestHash,
          },
        ],
        rowCount: 1,
      };
    }
    throw new Error(`Unexpected query: ${sql}`);
  });
  const database = {
    withTideTransaction: (
      work: (client: PoolClient) => Promise<unknown>,
    ): Promise<unknown> => work({ query } as unknown as PoolClient),
  } as unknown as DatabaseService;
  return { repository: new FileRepository(database), query };
}

function executedSql(query: jest.Mock): string {
  return (query.mock.calls as unknown as Array<[string]>)
    .map(([sql]) => sql)
    .join('\n');
}

describe('FileRepository upload task authorization', () => {
  it('rejects a pending P-FB-NEGATIVE assignment with a non-authorized label', async () => {
    const { repository, query } = createRepository({
      taskCode: 'P-FB-NEGATIVE',
      contentConfig: {
        contentStatus: 'PENDING',
        pendingReason: 'JIAHE_PERSONALIZED_CONTENT_PENDING',
      },
      evidenceSnapshot: {
        signal_samples: [
          { evidence: { negative_feedback_label: '教学节奏过快' } },
        ],
      },
    });

    await expect(repository.createIntent(input)).resolves.toEqual({
      type: 'TASK_STEP_NOT_FOUND',
    });
    expect(executedSql(query)).not.toContain('INSERT INTO tide.file_objects');
  });

  it('allows the P-FB environment photo step for the exact stable variant', async () => {
    const { repository, query } = createRepository({
      taskCode: 'P-FB-NEGATIVE',
      contentConfig: {
        contentStatus: 'PENDING',
        pendingReason: 'JIAHE_PERSONALIZED_CONTENT_PENDING',
      },
      evidenceSnapshot: {
        teacher_execution_variant: 'TEACHING_ENVIRONMENT_PHOTO',
      },
    });

    await expect(repository.createIntent(input)).resolves.toMatchObject({
      type: 'CREATED',
      file: { fileId: input.fileId },
    });
    expect(executedSql(query)).toContain('INSERT INTO tide.file_objects');
  });

  it.each([
    ['G04', 'g02-environment-photo'],
    ['G01', 'profile-evidence'],
  ])('keeps READY upload task %s authorized', async (taskCode, stepKey) => {
    const { repository, query } = createRepository({
      taskCode,
      contentConfig: { contentStatus: 'READY' },
      evidenceSnapshot: {},
    });

    await expect(
      repository.createIntent({ ...input, stepKey }),
    ).resolves.toMatchObject({ type: 'CREATED' });
    expect(executedSql(query)).toContain('INSERT INTO tide.file_objects');
  });
});
