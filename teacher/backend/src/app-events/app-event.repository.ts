import { Injectable } from '@nestjs/common';
import { createHash, randomUUID } from 'node:crypto';
import type { QueryResultRow } from 'pg';
import { DatabaseService } from '../platform/database/database.service';
import type { CreateAppEventDto } from './dto/create-app-event.dto';
import type { AppEventAccepted, SystemAppEventInput } from './app-event.models';
import { APP_EVENT_SCHEMA_VERSION } from './app-event.dictionary';

interface BindingRow extends QueryResultRow {
  bindingId: string;
  teacherId: string;
}

interface TaskAnalyticsRow extends QueryResultRow {
  taskCode: string;
  taskType: string;
  templateVersion: number;
  executionContractVersion: string;
  taskStatus: string;
  attemptNo: number;
  stepType: string | null;
}

interface BatchTaskAnalyticsRow extends TaskAnalyticsRow {
  taskAssignmentId: string;
  stepKey: string | null;
}

export class AppEventOwnershipError extends Error {}

@Injectable()
export class AppEventRepository {
  constructor(private readonly database: DatabaseService) {}

  saveClient(
    accountId: string,
    authenticatedSessionId: string,
    input: CreateAppEventDto,
  ): Promise<AppEventAccepted> {
    return this.database.withTideTransaction(async (client) => {
      const binding = await client.query<BindingRow>(
        `
          SELECT id AS "bindingId", teacher_id AS "teacherId"
          FROM tide.teacher_bindings
          WHERE account_id = $1 AND status = 'ACTIVE'
          LIMIT 1
        `,
        [accountId],
      );
      const owner = binding.rows[0];
      if (!owner) throw new AppEventOwnershipError();

      const taskProperties = await this.taskProperties(
        client,
        input.taskAssignmentId ?? null,
        owner.teacherId,
        typeof input.properties.stepKey === 'string'
          ? input.properties.stepKey
          : null,
      );
      await this.insert(client, {
        bindingId: owner.bindingId,
        anonymousActorId: this.actorId(owner.bindingId),
        eventName: input.eventName,
        eventId: input.eventId,
        eventSchemaVersion: input.eventSchemaVersion,
        source: 'CLIENT',
        sessionId: input.sessionId || authenticatedSessionId,
        taskAssignmentId: input.taskAssignmentId ?? null,
        properties: { ...input.properties, ...taskProperties },
        occurredAt: new Date(input.occurredAt),
      });
      return { accepted: true, eventId: input.eventId };
    });
  }

  saveClientBatch(
    accountId: string,
    authenticatedSessionId: string,
    indexedInputs: Array<{ index: number; input: CreateAppEventDto }>,
  ): Promise<{
    acceptedEventIds: string[];
    ownershipFailureIndexes: number[];
  }> {
    return this.database.withTideTransaction(async (client) => {
      const binding = await client.query<BindingRow>(
        `
          SELECT id AS "bindingId", teacher_id AS "teacherId"
          FROM tide.teacher_bindings
          WHERE account_id = $1 AND status = 'ACTIVE'
          LIMIT 1
        `,
        [accountId],
      );
      const owner = binding.rows[0];
      if (!owner) {
        return {
          acceptedEventIds: [],
          ownershipFailureIndexes: indexedInputs.map((item) => item.index),
        };
      }

      const taskAssignmentIds = [
        ...new Set(
          indexedInputs
            .map((item) => item.input.taskAssignmentId)
            .filter((value): value is string => Boolean(value)),
        ),
      ];
      const taskRows =
        taskAssignmentIds.length === 0
          ? []
          : (
              await client.query<BatchTaskAnalyticsRow>(
                `
                  SELECT
                    assignment.assignment_id AS "taskAssignmentId",
                    assignment.task_code AS "taskCode",
                    assignment.task_kind AS "taskType",
                    template.template_version AS "templateVersion",
                    execution.execution_contract_version
                      AS "executionContractVersion",
                    assignment.status AS "taskStatus",
                    COALESCE((
                      SELECT max(attempt.attempt_no)
                      FROM tide.task_attempts attempt
                      WHERE attempt.task_assignment_id =
                        assignment.assignment_id
                    ), 0) AS "attemptNo",
                    step.step_key AS "stepKey",
                    step.step_type AS "stepType"
                  FROM public.task_assignments assignment
                  JOIN public.task_templates template
                    ON template.row_id = assignment.template_version_id
                  JOIN tide.task_execution_versions execution
                    ON execution.shared_template_row_id =
                      assignment.template_version_id
                  LEFT JOIN tide.task_step_definitions step
                    ON step.execution_version_id = execution.id
                  WHERE assignment.assignment_id = ANY($1::varchar[])
                    AND assignment.teacher_id = $2
                `,
                [taskAssignmentIds, owner.teacherId],
              )
            ).rows;
      const taskRowsByAssignment = new Map<string, BatchTaskAnalyticsRow[]>();
      taskRows.forEach((row) => {
        const rows = taskRowsByAssignment.get(row.taskAssignmentId) ?? [];
        rows.push(row);
        taskRowsByAssignment.set(row.taskAssignmentId, rows);
      });

      const ownershipFailureIndexes: number[] = [];
      const rows = indexedInputs.flatMap(({ index, input }) => {
        const taskAssignmentId = input.taskAssignmentId ?? null;
        const candidates = taskAssignmentId
          ? taskRowsByAssignment.get(taskAssignmentId)
          : undefined;
        if (taskAssignmentId && (!candidates || candidates.length === 0)) {
          ownershipFailureIndexes.push(index);
          return [];
        }
        const stepKey =
          typeof input.properties.stepKey === 'string'
            ? input.properties.stepKey
            : null;
        const task = candidates?.[0];
        const step = stepKey
          ? candidates?.find((candidate) => candidate.stepKey === stepKey)
          : undefined;
        const taskProperties = task
          ? {
              taskCode: task.taskCode,
              taskType: task.taskType,
              templateVersion: Number(task.templateVersion),
              executionContractVersion: task.executionContractVersion,
              taskStatus: task.taskStatus,
              attemptNo: Number(task.attemptNo),
              ...(step?.stepType ? { stepType: step.stepType } : {}),
            }
          : {};
        return [
          {
            bindingId: owner.bindingId,
            anonymousActorId: this.actorId(owner.bindingId),
            eventName: input.eventName,
            eventId: input.eventId,
            eventSchemaVersion: input.eventSchemaVersion,
            source: 'CLIENT' as const,
            sessionId: input.sessionId || authenticatedSessionId,
            taskAssignmentId,
            properties: { ...input.properties, ...taskProperties },
            occurredAt: new Date(input.occurredAt),
          },
        ];
      });
      await this.insertBatch(client, rows);
      return {
        acceptedEventIds: rows.map((row) => row.eventId),
        ownershipFailureIndexes,
      };
    });
  }

  saveSystem(input: SystemAppEventInput): Promise<AppEventAccepted> {
    return this.database.withTideTransaction(async (client) => {
      let bindingId: string | null = null;
      let teacherId: string | null = null;
      if (input.accountId) {
        const binding = await client.query<BindingRow>(
          `
            SELECT id AS "bindingId", teacher_id AS "teacherId"
            FROM tide.teacher_bindings
            WHERE account_id = $1 AND status = 'ACTIVE'
            LIMIT 1
          `,
          [input.accountId],
        );
        bindingId = binding.rows[0]?.bindingId ?? null;
        teacherId = binding.rows[0]?.teacherId ?? null;
      }
      const anonymousActorId = bindingId
        ? this.actorId(bindingId)
        : (input.anonymousActorId ?? this.actorId(input.sessionId));
      const taskProperties = await this.taskProperties(
        client,
        input.taskAssignmentId ?? null,
        teacherId,
        typeof input.properties?.stepKey === 'string'
          ? input.properties.stepKey
          : null,
      );
      const eventId = input.eventId ?? `server-event-${randomUUID()}`;
      await this.insert(client, {
        bindingId,
        anonymousActorId,
        eventName: input.eventName,
        eventId,
        eventSchemaVersion: APP_EVENT_SCHEMA_VERSION,
        source: 'BACKEND',
        sessionId: input.sessionId,
        taskAssignmentId: input.taskAssignmentId ?? null,
        properties: { ...(input.properties ?? {}), ...taskProperties },
        occurredAt: input.occurredAt ?? new Date(),
      });
      return { accepted: true, eventId };
    });
  }

  saveAnonymousClient(input: CreateAppEventDto): Promise<AppEventAccepted> {
    return this.database.withTideTransaction(async (client) => {
      await this.insert(client, {
        bindingId: null,
        anonymousActorId: this.actorId(input.sessionId),
        eventName: input.eventName,
        eventId: input.eventId,
        eventSchemaVersion: input.eventSchemaVersion,
        source: 'CLIENT',
        sessionId: input.sessionId,
        taskAssignmentId: null,
        properties: input.properties,
        occurredAt: new Date(input.occurredAt),
      });
      return { accepted: true, eventId: input.eventId };
    });
  }

  saveAnonymousClientBatch(inputs: CreateAppEventDto[]): Promise<string[]> {
    return this.database.withTideTransaction(async (client) => {
      const rows = inputs.map((input) => ({
        bindingId: null,
        anonymousActorId: this.actorId(input.sessionId),
        eventName: input.eventName,
        eventId: input.eventId,
        eventSchemaVersion: input.eventSchemaVersion,
        source: 'CLIENT' as const,
        sessionId: input.sessionId,
        taskAssignmentId: null,
        properties: input.properties,
        occurredAt: new Date(input.occurredAt),
      }));
      await this.insertBatch(client, rows);
      return rows.map((row) => row.eventId);
    });
  }

  private async taskProperties(
    client: {
      query: <T extends QueryResultRow>(
        text: string,
        values?: unknown[],
      ) => Promise<{ rows: T[]; rowCount: number | null }>;
    },
    taskAssignmentId: string | null,
    teacherId: string | null,
    stepKey: string | null,
  ): Promise<Record<string, unknown>> {
    if (!taskAssignmentId) return {};
    if (!teacherId) throw new AppEventOwnershipError();
    const task = await client.query<TaskAnalyticsRow>(
      `
        SELECT
          assignment.task_code AS "taskCode",
          assignment.task_kind AS "taskType",
          template.template_version AS "templateVersion",
          execution.execution_contract_version AS "executionContractVersion",
          assignment.status AS "taskStatus",
          COALESCE((
            SELECT max(attempt.attempt_no)
            FROM tide.task_attempts attempt
            WHERE attempt.task_assignment_id = assignment.assignment_id
          ), 0) AS "attemptNo",
          (
            SELECT step.step_type
            FROM tide.task_step_definitions step
            WHERE step.execution_version_id = execution.id
              AND step.step_key = $3
            LIMIT 1
          ) AS "stepType"
        FROM public.task_assignments assignment
        JOIN public.task_templates template
          ON template.row_id = assignment.template_version_id
        JOIN tide.task_execution_versions execution
          ON execution.shared_template_row_id = assignment.template_version_id
        WHERE assignment.assignment_id = $1
          AND assignment.teacher_id = $2
        LIMIT 1
      `,
      [taskAssignmentId, teacherId, stepKey],
    );
    const row = task.rows[0];
    if (!row) throw new AppEventOwnershipError();
    return {
      taskCode: row.taskCode,
      taskType: row.taskType,
      templateVersion: Number(row.templateVersion),
      executionContractVersion: row.executionContractVersion,
      taskStatus: row.taskStatus,
      attemptNo: Number(row.attemptNo),
      ...(row.stepType ? { stepType: row.stepType } : {}),
    };
  }

  private insert(
    client: {
      query: (
        text: string,
        values?: unknown[],
      ) => Promise<{ rowCount: number | null }>;
    },
    input: {
      bindingId: string | null;
      anonymousActorId: string;
      eventName: string;
      eventId: string;
      eventSchemaVersion: number;
      source: 'CLIENT' | 'BACKEND';
      sessionId: string;
      taskAssignmentId: string | null;
      properties: Record<string, unknown>;
      occurredAt: Date;
    },
  ): Promise<{ rowCount: number | null }> {
    return client.query(
      `
        INSERT INTO tide.app_events (
          id, teacher_binding_id, anonymous_teacher_id,
          event_name, event_id, event_schema_version, event_source,
          session_id, task_assignment_id, properties, occurred_at
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
        ON CONFLICT DO NOTHING
      `,
      [
        randomUUID(),
        input.bindingId,
        input.anonymousActorId,
        input.eventName,
        input.eventId,
        input.eventSchemaVersion,
        input.source,
        input.sessionId,
        input.taskAssignmentId,
        input.properties,
        input.occurredAt,
      ],
    );
  }

  private insertBatch(
    client: {
      query: (
        text: string,
        values?: unknown[],
      ) => Promise<{ rowCount: number | null }>;
    },
    inputs: Array<{
      bindingId: string | null;
      anonymousActorId: string;
      eventName: string;
      eventId: string;
      eventSchemaVersion: number;
      source: 'CLIENT' | 'BACKEND';
      sessionId: string;
      taskAssignmentId: string | null;
      properties: Record<string, unknown>;
      occurredAt: Date;
    }>,
  ): Promise<{ rowCount: number | null }> {
    if (inputs.length === 0) return Promise.resolve({ rowCount: 0 });
    return client.query(
      `
        INSERT INTO tide.app_events (
          id, teacher_binding_id, anonymous_teacher_id,
          event_name, event_id, event_schema_version, event_source,
          session_id, task_assignment_id, properties, occurred_at
        )
        SELECT
          input.id,
          input.binding_id,
          input.anonymous_actor_id,
          input.event_name,
          input.event_id,
          input.event_schema_version,
          input.event_source,
          input.session_id,
          input.task_assignment_id,
          input.properties::jsonb,
          input.occurred_at
        FROM unnest(
          $1::uuid[],
          $2::uuid[],
          $3::text[],
          $4::text[],
          $5::text[],
          $6::integer[],
          $7::text[],
          $8::text[],
          $9::varchar[],
          $10::text[],
          $11::timestamptz[]
        ) AS input(
          id,
          binding_id,
          anonymous_actor_id,
          event_name,
          event_id,
          event_schema_version,
          event_source,
          session_id,
          task_assignment_id,
          properties,
          occurred_at
        )
        ON CONFLICT DO NOTHING
      `,
      [
        inputs.map(() => randomUUID()),
        inputs.map((input) => input.bindingId),
        inputs.map((input) => input.anonymousActorId),
        inputs.map((input) => input.eventName),
        inputs.map((input) => input.eventId),
        inputs.map((input) => input.eventSchemaVersion),
        inputs.map((input) => input.source),
        inputs.map((input) => input.sessionId),
        inputs.map((input) => input.taskAssignmentId),
        inputs.map((input) => JSON.stringify(input.properties)),
        inputs.map((input) => input.occurredAt),
      ],
    );
  }

  private actorId(value: string): string {
    return createHash('sha256').update(`tide-analytics:${value}`).digest('hex');
  }
}
