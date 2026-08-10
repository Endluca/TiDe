import { Injectable } from '@nestjs/common';
import { randomUUID } from 'node:crypto';
import type { PoolClient, QueryResultRow } from 'pg';
import { DatabaseService } from '../../platform/database/database.service';
import type { TaskStatus } from '../../tasks/task.models';
import type {
  KuozhiProgressCore,
  KuozhiProgressResponse,
} from './kuozhi.models';

export class KuozhiProgressConflictError extends Error {
  constructor(
    public readonly reason: 'IDEMPOTENCY_CONFLICT' | 'STATE_VERSION_CONFLICT',
    public readonly details: Record<string, unknown> = {},
  ) {
    super(reason);
    this.name = 'KuozhiProgressConflictError';
  }
}

export class KuozhiOwnedTaskNotFoundError extends Error {
  constructor() {
    super('Owned task was not found');
    this.name = 'KuozhiOwnedTaskNotFoundError';
  }
}

interface AssignmentRow extends QueryResultRow {
  status: TaskStatus;
  stateVersion: string;
}

interface ReceiptRow extends QueryResultRow {
  taskAssignmentId: string;
  idempotencyKey: string;
  commandId: string;
  requestHash: string;
  responseBody: KuozhiProgressResponse;
}

export interface PersistKuozhiProgressInput {
  accountId: string;
  taskInstanceId: string;
  expectedStateVersion: number;
  idempotencyKey: string;
  commandId: string;
  requestHash: string;
  autoCompleteAssignment: boolean;
  progress: KuozhiProgressCore;
}

@Injectable()
export class KuozhiProgressRepository {
  constructor(private readonly database: DatabaseService) {}

  async getLatest(
    accountId: string,
    taskInstanceId: string,
  ): Promise<KuozhiProgressResponse | null> {
    const result = await this.database.queryTide<
      {
        responseBody: KuozhiProgressResponse;
      } & QueryResultRow
    >(
      `
        SELECT sync.response_body AS "responseBody"
        FROM tide.kuozhi_course_syncs sync
        JOIN public.task_assignments assignment
          ON assignment.assignment_id = sync.task_assignment_id
        JOIN tide.teacher_bindings binding
          ON binding.teacher_id = assignment.teacher_id
         AND binding.account_id = $1
         AND binding.status = 'ACTIVE'
        WHERE sync.task_assignment_id = $2
        ORDER BY sync.created_at DESC, sync.id DESC
        LIMIT 1
      `,
      [accountId, taskInstanceId],
    );
    return result.rows[0]?.responseBody ?? null;
  }

  persistRefresh(
    input: PersistKuozhiProgressInput,
  ): Promise<KuozhiProgressResponse> {
    return this.database.withTideTransaction(async (client) => {
      const replay = await this.findReplay(client, input);
      if (replay) return replay;

      const assignment = await this.lockOwnedAssignment(client, input);
      const actualVersion = Number(assignment.stateVersion);
      if (actualVersion !== input.expectedStateVersion) {
        throw new KuozhiProgressConflictError('STATE_VERSION_CONFLICT', {
          expectedStateVersion: input.expectedStateVersion,
          actualStateVersion: actualVersion,
        });
      }

      let status = assignment.status;
      let stateVersion = actualVersion;
      let stateUpdated = false;
      if (
        input.autoCompleteAssignment &&
        input.progress.completion.completed &&
        ['ASSIGNED', 'VIEWED', 'IN_PROGRESS'].includes(status)
      ) {
        const updated = await client.query<AssignmentRow>(
          `
            UPDATE public.task_assignments
            SET status = 'COMPLETED',
                status_reason_code = NULL,
                status_changed_at = GREATEST(
                  clock_timestamp(),
                  status_changed_at + interval '1 microsecond'
                ),
                completed_at = clock_timestamp(),
                updated_by = 'tide_teacher_backend'
            WHERE assignment_id = $1
              AND row_version = $2
            RETURNING status, row_version AS "stateVersion"
          `,
          [input.taskInstanceId, input.expectedStateVersion],
        );
        if (!updated.rows[0]) {
          throw new KuozhiProgressConflictError('STATE_VERSION_CONFLICT', {
            expectedStateVersion: input.expectedStateVersion,
          });
        }
        status = updated.rows[0].status;
        stateVersion = Number(updated.rows[0].stateVersion);
        stateUpdated = true;
      }

      const response: KuozhiProgressResponse = {
        ...input.progress,
        assignment: { status, stateVersion, stateUpdated },
      };
      await client.query(
        `
          INSERT INTO tide.kuozhi_course_syncs (
            id, account_id, task_assignment_id, idempotency_key,
            command_id, request_hash, mapping_version, sync_status,
            completion_decision, response_body
          ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
        `,
        [
          randomUUID(),
          input.accountId,
          input.taskInstanceId,
          input.idempotencyKey,
          input.commandId,
          input.requestHash,
          input.progress.mappingVersion,
          input.progress.syncStatus,
          input.progress.completion.completed,
          response,
        ],
      );
      return response;
    });
  }

  private async findReplay(
    client: PoolClient,
    input: PersistKuozhiProgressInput,
  ): Promise<KuozhiProgressResponse | null> {
    const lockKeys = [
      `kuozhi-command:${input.accountId}:${input.commandId}`,
      `kuozhi-idempotency:${input.accountId}:${input.idempotencyKey}`,
    ].sort();
    for (const key of lockKeys) {
      await client.query(
        'SELECT pg_advisory_xact_lock(hashtextextended($1, 0))',
        [key],
      );
    }
    const existing = await client.query<ReceiptRow>(
      `
        SELECT
          task_assignment_id AS "taskAssignmentId",
          idempotency_key AS "idempotencyKey",
          command_id AS "commandId",
          request_hash AS "requestHash",
          response_body AS "responseBody"
        FROM tide.kuozhi_course_syncs
        WHERE account_id = $1
          AND (idempotency_key = $2 OR command_id = $3)
      `,
      [input.accountId, input.idempotencyKey, input.commandId],
    );
    if (existing.rows.length === 0) return null;
    const receipt = existing.rows[0];
    if (
      existing.rows.length !== 1 ||
      receipt.taskAssignmentId !== input.taskInstanceId ||
      receipt.idempotencyKey !== input.idempotencyKey ||
      receipt.commandId !== input.commandId ||
      receipt.requestHash !== input.requestHash
    ) {
      throw new KuozhiProgressConflictError('IDEMPOTENCY_CONFLICT');
    }
    return receipt.responseBody;
  }

  private async lockOwnedAssignment(
    client: PoolClient,
    input: Pick<PersistKuozhiProgressInput, 'accountId' | 'taskInstanceId'>,
  ): Promise<AssignmentRow> {
    const result = await client.query<AssignmentRow>(
      `
        SELECT assignment.status, assignment.row_version AS "stateVersion"
        FROM public.task_assignments assignment
        JOIN tide.teacher_bindings binding
          ON binding.teacher_id = assignment.teacher_id
         AND binding.account_id = $2
         AND binding.status = 'ACTIVE'
        WHERE assignment.assignment_id = $1
        FOR UPDATE OF assignment
      `,
      [input.taskInstanceId, input.accountId],
    );
    if (!result.rows[0]) throw new KuozhiOwnedTaskNotFoundError();
    return result.rows[0];
  }
}
