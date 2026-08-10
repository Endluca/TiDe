import { Injectable } from '@nestjs/common';
import { randomUUID } from 'node:crypto';
import type { PoolClient, QueryResultRow } from 'pg';
import { DatabaseService } from '../platform/database/database.service';
import {
  publicationActionTarget,
  publicationConfigHash,
  type SystemNotificationActionType,
  type SystemNotificationPublicationConfig,
} from './system-notification.config';

interface PublicationRow extends QueryResultRow {
  publicationId: string;
  configKey: string;
  typeCode: string;
  title: string;
  body: string;
  audience: Record<string, unknown>;
  actionType: SystemNotificationActionType | null;
  actionTarget: string | null;
  scheduledAt: Date;
  expiresAt: Date | null;
  status: 'SCHEDULED' | 'PUBLISHED' | 'CANCELLED' | 'FAILED';
  configHash: string;
  attemptCount: number;
}

interface RecipientRow extends QueryResultRow {
  teacherId: string;
}

interface TaskTargetRow extends QueryResultRow {
  teacherId: string;
  assignmentId: string;
}

@Injectable()
export class SystemNotificationRepository {
  constructor(private readonly database: DatabaseService) {}

  async syncPublication(
    publication: SystemNotificationPublicationConfig,
  ): Promise<void> {
    const hash = publicationConfigHash(publication);
    await this.database.withTideTransaction(async (client) => {
      const current = await client.query<PublicationRow>(
        `
          SELECT
            publication_id AS "publicationId", config_key AS "configKey",
            type_code AS "typeCode", title, body, audience,
            action_type AS "actionType", action_target AS "actionTarget",
            scheduled_at AS "scheduledAt", expires_at AS "expiresAt",
            status, config_hash AS "configHash",
            attempt_count AS "attemptCount"
          FROM tide.system_notification_publications
          WHERE config_key = $1
          FOR UPDATE
        `,
        [publication.configKey],
      );
      const existing = current.rows[0];
      if (!existing) {
        await this.insertPublication(client, publication, hash);
        return;
      }

      if (publication.cancelled) {
        await this.cancelPublication(client, existing.publicationId);
        return;
      }

      if (existing.status === 'PUBLISHED' || existing.status === 'CANCELLED') {
        if (existing.configHash !== hash) {
          throw new Error(`已发布系统通知 ${publication.configKey} 不允许修改`);
        }
        return;
      }

      if (
        (existing.status === 'SCHEDULED' || existing.status === 'FAILED') &&
        existing.configHash === hash
      ) {
        return;
      }

      await client.query(
        `
          UPDATE tide.system_notification_publications
          SET type_code = $2, title = $3, body = $4, audience = $5,
              action_type = $6, action_target = $7,
              scheduled_at = $8, expires_at = $9,
              status = 'SCHEDULED', config_hash = $10,
              attempt_count = 0, next_attempt_at = NULL,
              last_error = NULL, updated_at = now()
          WHERE publication_id = $1
        `,
        [
          existing.publicationId,
          publication.typeCode,
          publication.title,
          publication.body,
          publication.audience,
          publication.action?.type ?? null,
          publicationActionTarget(publication.action),
          new Date(publication.publishAt),
          publication.expiresAt ? new Date(publication.expiresAt) : null,
          hash,
        ],
      );
    });
  }

  async publishDue(
    limit: number,
    assertLeaseActive: () => void = () => undefined,
  ): Promise<number> {
    let published = 0;
    for (let index = 0; index < limit; index += 1) {
      assertLeaseActive();
      const publicationId = await this.nextDuePublicationId();
      if (!publicationId) break;
      assertLeaseActive();
      try {
        const didPublish = await this.publishOne(publicationId);
        if (didPublish) published += 1;
      } catch (error) {
        await this.recordFailure(publicationId, error);
      }
      assertLeaseActive();
    }
    return published;
  }

  private insertPublication(
    client: PoolClient,
    publication: SystemNotificationPublicationConfig,
    hash: string,
  ): Promise<unknown> {
    const cancelledAt = publication.cancelled ? new Date() : null;
    return client.query(
      `
        INSERT INTO tide.system_notification_publications (
          publication_id, config_key, type_code, title, body, audience,
          action_type, action_target, scheduled_at, expires_at,
          status, config_hash, cancelled_at
        ) VALUES (
          $1, $2, $3, $4, $5, $6, $7, $8, $9, $10,
          $11, $12, $13
        )
      `,
      [
        randomUUID(),
        publication.configKey,
        publication.typeCode,
        publication.title,
        publication.body,
        publication.audience,
        publication.action?.type ?? null,
        publicationActionTarget(publication.action),
        new Date(publication.publishAt),
        publication.expiresAt ? new Date(publication.expiresAt) : null,
        publication.cancelled ? 'CANCELLED' : 'SCHEDULED',
        hash,
        cancelledAt,
      ],
    );
  }

  private async cancelPublication(
    client: PoolClient,
    publicationId: string,
  ): Promise<void> {
    await client.query(
      `
        UPDATE tide.system_notification_publications
        SET status = 'CANCELLED', cancelled_at = COALESCE(cancelled_at, now()),
            updated_at = now()
        WHERE publication_id = $1
          AND status <> 'CANCELLED'
      `,
      [publicationId],
    );
    await client.query(
      `
        UPDATE tide.system_notifications
        SET cancelled_at = COALESCE(cancelled_at, now()), updated_at = now()
        WHERE publication_id = $1
      `,
      [publicationId],
    );
  }

  private async nextDuePublicationId(): Promise<string | null> {
    const result = await this.database.queryTide<{ publicationId: string }>(
      `
        SELECT publication_id AS "publicationId"
        FROM tide.system_notification_publications
        WHERE status = 'SCHEDULED'
          AND COALESCE(next_attempt_at, scheduled_at) <= now()
        ORDER BY COALESCE(next_attempt_at, scheduled_at), publication_id
        LIMIT 1
      `,
    );
    return result.rows[0]?.publicationId ?? null;
  }

  private publishOne(publicationId: string): Promise<boolean> {
    return this.database.withTideTransaction(async (client) => {
      const result = await client.query<PublicationRow>(
        `
          SELECT
            publication_id AS "publicationId", config_key AS "configKey",
            type_code AS "typeCode", title, body, audience,
            action_type AS "actionType", action_target AS "actionTarget",
            scheduled_at AS "scheduledAt", expires_at AS "expiresAt",
            status, config_hash AS "configHash",
            attempt_count AS "attemptCount"
          FROM tide.system_notification_publications
          WHERE publication_id = $1
            AND status = 'SCHEDULED'
            AND COALESCE(next_attempt_at, scheduled_at) <= now()
          FOR UPDATE SKIP LOCKED
        `,
        [publicationId],
      );
      const publication = result.rows[0];
      if (!publication) return false;

      const recipients = await this.resolveRecipients(
        client,
        publication.audience,
      );
      const taskTargets =
        publication.actionType === 'TASK_DETAIL' && publication.actionTarget
          ? await this.resolveTaskTargets(
              client,
              recipients,
              publication.actionTarget,
            )
          : new Map<string, string>();

      for (const teacherId of recipients) {
        const action = this.deliveryAction(
          publication.actionType,
          publication.actionTarget,
          taskTargets.get(teacherId),
        );
        await client.query(
          `
            INSERT INTO tide.system_notifications (
              system_notification_id, publication_id, teacher_id,
              type_code, title, body, action_type, action_target,
              expires_at, dedupe_key, payload, issued_at
            ) VALUES (
              $1, $2, $3, $4, $5, $6, $7, $8, $9, $10,
              jsonb_build_object('publicationConfigKey', $11::text), now()
            )
            ON CONFLICT (dedupe_key) DO NOTHING
          `,
          [
            randomUUID(),
            publication.publicationId,
            teacherId,
            publication.typeCode,
            publication.title,
            publication.body,
            action.type,
            action.target,
            publication.expiresAt,
            `publication:${publication.publicationId}:${teacherId}`,
            publication.configKey,
          ],
        );
      }

      await client.query(
        `
          UPDATE tide.system_notification_publications
          SET status = 'PUBLISHED', recipient_count = $2,
              published_at = now(), next_attempt_at = NULL,
              last_error = NULL, updated_at = now()
          WHERE publication_id = $1
        `,
        [publication.publicationId, recipients.length],
      );
      return true;
    });
  }

  private async resolveRecipients(
    client: PoolClient,
    audience: Record<string, unknown>,
  ): Promise<string[]> {
    const conditions: string[] = [];
    const values: unknown[] = [];
    const addValue = (value: unknown): string => {
      values.push(value);
      return `$${values.length}`;
    };

    if (audience.all !== true) {
      const teacherIds = this.stringArray(audience.teacherIds);
      if (teacherIds.length > 0) {
        conditions.push(
          `teacher.teacher_id = ANY(${addValue(teacherIds)}::varchar[])`,
        );
      }
      const campDay = audience.campDay as
        { min?: unknown; max?: unknown } | undefined;
      if (
        campDay &&
        typeof campDay.min === 'number' &&
        typeof campDay.max === 'number'
      ) {
        conditions.push(
          `teacher.camp_day BETWEEN ${addValue(campDay.min)} AND ${addValue(campDay.max)}`,
        );
      }
      const accountStatuses = this.stringArray(audience.accountStatuses);
      if (accountStatuses.length > 0) {
        conditions.push(`
          EXISTS (
            SELECT 1
            FROM tide.teacher_bindings binding
            WHERE binding.teacher_id = teacher.teacher_id
              AND binding.status = ANY(${addValue(accountStatuses)}::text[])
          )
        `);
      }
      const task = audience.task as
        { taskCodes?: unknown; statuses?: unknown } | undefined;
      const taskCodes = this.stringArray(task?.taskCodes);
      const statuses = this.stringArray(task?.statuses);
      if (taskCodes.length > 0 && statuses.length > 0) {
        conditions.push(`
          EXISTS (
            SELECT 1
            FROM public.task_assignments assignment
            WHERE assignment.teacher_id = teacher.teacher_id
              AND assignment.task_code = ANY(${addValue(taskCodes)}::varchar[])
              AND assignment.status = ANY(${addValue(statuses)}::varchar[])
          )
        `);
      }
    }

    const result = await client.query<RecipientRow>(
      `
        SELECT teacher.teacher_id AS "teacherId"
        FROM public.teachers teacher
        ${conditions.length > 0 ? `WHERE ${conditions.join(' AND ')}` : ''}
        ORDER BY teacher.teacher_id
      `,
      values,
    );
    return result.rows.map((row) => row.teacherId);
  }

  private async resolveTaskTargets(
    client: PoolClient,
    teacherIds: string[],
    taskCode: string,
  ): Promise<Map<string, string>> {
    if (teacherIds.length === 0) return new Map();
    const result = await client.query<TaskTargetRow>(
      `
        SELECT DISTINCT ON (teacher_id)
          teacher_id AS "teacherId", assignment_id AS "assignmentId"
        FROM public.task_assignments
        WHERE teacher_id = ANY($1::varchar[])
          AND task_code = $2
          AND status NOT IN ('CANCELLED', 'EXPIRED')
        ORDER BY teacher_id, assigned_at DESC, assignment_id DESC
      `,
      [teacherIds, taskCode],
    );
    return new Map(result.rows.map((row) => [row.teacherId, row.assignmentId]));
  }

  private deliveryAction(
    actionType: SystemNotificationActionType | null,
    actionTarget: string | null,
    taskAssignmentId: string | undefined,
  ): { type: SystemNotificationActionType | null; target: string | null } {
    if (!actionType || !actionTarget) return { type: null, target: null };
    if (actionType === 'TASK_DETAIL') {
      return taskAssignmentId
        ? { type: actionType, target: `/task/${taskAssignmentId}` }
        : { type: null, target: null };
    }
    return { type: actionType, target: actionTarget };
  }

  private async recordFailure(
    publicationId: string,
    error: unknown,
  ): Promise<void> {
    const message =
      error instanceof Error ? error.message.slice(0, 1_000) : 'Unknown error';
    await this.database.queryTide(
      `
        UPDATE tide.system_notification_publications
        SET attempt_count = attempt_count + 1,
            status = CASE WHEN attempt_count + 1 >= 10 THEN 'FAILED' ELSE 'SCHEDULED' END,
            next_attempt_at = CASE
              WHEN attempt_count + 1 >= 10 THEN NULL
              ELSE now() + make_interval(
                secs => LEAST(3600, 30 * power(2, LEAST(attempt_count, 7))::integer)
              )
            END,
            last_error = $2, updated_at = now()
        WHERE publication_id = $1
          AND status = 'SCHEDULED'
      `,
      [publicationId, message],
    );
  }

  private stringArray(value: unknown): string[] {
    return Array.isArray(value)
      ? value.filter((item): item is string => typeof item === 'string')
      : [];
  }
}
