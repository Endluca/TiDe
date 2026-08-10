import { Injectable } from '@nestjs/common';
import { createHash, randomUUID } from 'node:crypto';
import type { QueryResultRow } from 'pg';
import { DatabaseService } from '../platform/database/database.service';
import type { NotificationResponse } from './tide.models';

export interface TeacherBindingIdentity {
  bindingId: string;
  teacherId: string;
  email: string;
}

export interface G01Evidence {
  selfIntroduced: boolean | null;
  tesolCompleted: boolean | null;
  sourceUpdatedAt: null;
}

export interface FixedGrowthTask {
  taskCode: string;
  title: string | null;
  status: string;
  taskUpdatedAt: string;
}

interface BindingRow extends QueryResultRow, TeacherBindingIdentity {}

interface G01EvidenceRow extends QueryResultRow {
  selfIntroduced: boolean | null;
  tesolCompleted: boolean | null;
}

interface NotificationRow extends QueryResultRow {
  sourceNotificationId: string;
  source: 'EXTERNAL' | 'SYSTEM';
  typeCode: string | null;
  title: string;
  body: string;
  relatedTaskCode: string | null;
  relatedTaskInstanceId: string | null;
  actionType: NotificationResponse['actionType'];
  actionTarget: string | null;
  actionAvailable: boolean;
  expiresAt: Date | null;
  expired: boolean;
  issuedAt: Date;
  read: boolean;
  clicked: boolean;
}

interface NotificationCountsRow extends QueryResultRow {
  totalCount: string;
  unreadCount: string;
}

interface FixedGrowthTaskRow extends QueryResultRow {
  taskCode: string;
  title: string | null;
  status: string;
  taskUpdatedAt: Date;
}

export interface NotificationPage {
  items: NotificationResponse[];
  hasMore: boolean;
  totalCount: number;
  unreadCount: number;
}

export interface NotificationPageOptions {
  limit: number;
  unreadOnly: boolean;
  beforeIssuedAt: Date | null;
  beforeSourceNotificationId: string | null;
}

@Injectable()
export class TideRepository {
  constructor(private readonly database: DatabaseService) {}

  async findBinding(accountId: string): Promise<TeacherBindingIdentity | null> {
    const result = await this.database.queryTide<BindingRow>(
      `
        SELECT binding.id AS "bindingId", binding.teacher_id AS "teacherId",
          account.email
        FROM tide.teacher_bindings binding
        JOIN tide.user_accounts account ON account.id = binding.account_id
        WHERE binding.account_id = $1 AND binding.status = 'ACTIVE'
        LIMIT 1
      `,
      [accountId],
    );
    return result.rows[0] ?? null;
  }

  async findLatestG01Evidence(teacherId: string): Promise<G01Evidence | null> {
    const result = await this.database.queryTide<G01EvidenceRow>(
      `
        SELECT
          is_self_introduce AS "selfIntroduced",
          is_cpl_tesol AS "tesolCompleted"
        FROM public.teacher_source_wide
        WHERE tchr_id = $1
        LIMIT 1
      `,
      [teacherId],
    );
    const row = result.rows[0];
    return row
      ? {
          selfIntroduced: row.selfIntroduced,
          tesolCompleted: row.tesolCompleted,
          sourceUpdatedAt: null,
        }
      : null;
  }

  async listFixedGrowthTasks(teacherId: string): Promise<FixedGrowthTask[]> {
    const result = await this.database.queryTide<FixedGrowthTaskRow>(
      `
        SELECT
          assignment.task_code AS "taskCode",
          COALESCE(
            assignment.display_title,
            template.payload->>'title'
          ) AS title,
          assignment.status,
          assignment.updated_at AS "taskUpdatedAt"
        FROM public.task_assignments assignment
        JOIN public.task_templates template
          ON template.row_id = assignment.template_version_id
         AND template.status = 'PUBLISHED'
        WHERE assignment.teacher_id = $1
          AND assignment.task_code ~ '^G0[1-9]$'
        ORDER BY assignment.task_code
      `,
      [teacherId],
    );
    return result.rows.map((row) => ({
      taskCode: row.taskCode,
      title: row.title,
      status: row.status,
      taskUpdatedAt: row.taskUpdatedAt.toISOString(),
    }));
  }

  async listNotifications(
    teacherId: string,
    options: NotificationPageOptions,
  ): Promise<NotificationPage> {
    const [result, counts] = await Promise.all([
      this.database.queryTide<NotificationRow>(
        `
        WITH merged AS (
          SELECT
            'external:' || notification.notification_id AS "sourceNotificationId",
            'EXTERNAL'::text AS source,
            NULL::text AS "typeCode",
            COALESCE(notification.payload->>'title', 'Notification') AS title,
            COALESCE(notification.payload->>'body', '') AS body,
            NULL::text AS "relatedTaskCode",
            NULL::text AS "relatedTaskInstanceId",
            NULL::text AS "actionType",
            NULL::text AS "actionTarget",
            false AS "actionAvailable",
            notification.response_due_at AS "expiresAt",
            (
              notification.status = 'EXPIRED'
              OR (
                notification.response_due_at IS NOT NULL
                AND notification.response_due_at <= now()
              )
            ) AS expired,
            notification.requested_at AS "issuedAt",
            (notification.read_at IS NOT NULL) AS read,
            (notification.clicked_at IS NOT NULL) AS clicked
          FROM public.notifications notification
          WHERE notification.teacher_id = $1
            AND notification.status NOT IN ('CANCELLED', 'REVOKED')

          UNION ALL

          SELECT
            'system:' || notification.system_notification_id::text AS "sourceNotificationId",
            'SYSTEM'::text AS source,
            notification.type_code AS "typeCode",
            notification.title,
            notification.body,
            action_assignment.task_code AS "relatedTaskCode",
            action_assignment.assignment_id AS "relatedTaskInstanceId",
            notification.action_type AS "actionType",
            CASE
              WHEN notification.type_code = 'GROWTH_STAGE_AVAILABLE'
               AND (notification.payload->>'stageNumber') ~ '^[1-3]$'
                THEN '/path?stage=' || (notification.payload->>'stageNumber')
              ELSE notification.action_target
            END AS "actionTarget",
            (
              notification.action_type IS NOT NULL
              AND (notification.expires_at IS NULL OR notification.expires_at > now())
              AND (
                notification.action_type <> 'TASK_DETAIL'
                OR (
                  action_assignment.assignment_id IS NOT NULL
                  AND action_assignment.status NOT IN ('CANCELLED', 'EXPIRED')
                )
              )
            ) AS "actionAvailable",
            notification.expires_at AS "expiresAt",
            (
              notification.expires_at IS NOT NULL
              AND notification.expires_at <= now()
            ) AS expired,
            notification.issued_at AS "issuedAt",
            (notification.read_at IS NOT NULL) AS read,
            (notification.clicked_at IS NOT NULL) AS clicked
          FROM tide.system_notifications notification
          LEFT JOIN public.task_assignments action_assignment
            ON notification.action_type = 'TASK_DETAIL'
           AND notification.action_target = '/task/' || action_assignment.assignment_id
           AND action_assignment.teacher_id = notification.teacher_id
          WHERE notification.teacher_id = $1
            AND notification.cancelled_at IS NULL
        )
        SELECT *
        FROM merged
        WHERE (NOT $2::boolean OR NOT read)
          AND (
            $3::timestamptz IS NULL
            OR "issuedAt" < $3
            OR (
              "issuedAt" = $3
              AND "sourceNotificationId" < $4
            )
          )
        ORDER BY "issuedAt" DESC, "sourceNotificationId" DESC
        LIMIT $5
      `,
        [
          teacherId,
          options.unreadOnly,
          options.beforeIssuedAt,
          options.beforeSourceNotificationId,
          options.limit + 1,
        ],
      ),
      this.database.queryTide<NotificationCountsRow>(
        `
          SELECT
            (
              (
                SELECT count(*)
                FROM public.notifications
                WHERE teacher_id = $1
                  AND status NOT IN ('CANCELLED', 'REVOKED')
              )
              +
              (
                SELECT count(*)
                FROM tide.system_notifications
                WHERE teacher_id = $1 AND cancelled_at IS NULL
              )
            )::text AS "totalCount",
            (
              (
                SELECT count(*)
                FROM public.notifications
                WHERE teacher_id = $1
                  AND status NOT IN ('CANCELLED', 'REVOKED')
                  AND read_at IS NULL
              )
              +
              (
                SELECT count(*)
                FROM tide.system_notifications
                WHERE teacher_id = $1
                  AND cancelled_at IS NULL
                  AND read_at IS NULL
              )
            )::text AS "unreadCount"
        `,
        [teacherId],
      ),
    ]);
    const hasMore = result.rows.length > options.limit;
    const items = result.rows.slice(0, options.limit).map((row) => ({
      sourceNotificationId: row.sourceNotificationId,
      source: row.source,
      typeCode: row.typeCode,
      title: row.title,
      body: row.body,
      relatedTaskCode: row.relatedTaskCode,
      relatedTaskInstanceId: row.relatedTaskInstanceId,
      actionType: row.actionType,
      actionTarget: row.actionTarget,
      actionAvailable: row.actionAvailable,
      expiresAt: row.expiresAt?.toISOString() ?? null,
      expired: row.expired,
      issuedAt: row.issuedAt.toISOString(),
      read: row.read,
      clicked: row.clicked,
    }));
    return {
      items,
      hasMore,
      totalCount: Number(counts.rows[0]?.totalCount ?? 0),
      unreadCount: Number(counts.rows[0]?.unreadCount ?? 0),
    };
  }

  markNotificationRead(
    teacherId: string,
    sourceNotificationId: string,
  ): Promise<boolean> {
    if (sourceNotificationId.startsWith('system:')) {
      return this.markSystemNotification(
        teacherId,
        sourceNotificationId.slice('system:'.length),
        'READ',
      );
    }
    if (!sourceNotificationId.startsWith('external:'))
      return Promise.resolve(false);
    return this.markExternalNotification(
      teacherId,
      sourceNotificationId.slice('external:'.length),
      'READ',
    );
  }

  markNotificationClicked(
    teacherId: string,
    sourceNotificationId: string,
  ): Promise<boolean> {
    if (sourceNotificationId.startsWith('system:')) {
      return this.markSystemNotification(
        teacherId,
        sourceNotificationId.slice('system:'.length),
        'CLICKED',
      );
    }
    if (!sourceNotificationId.startsWith('external:'))
      return Promise.resolve(false);
    return this.markExternalNotification(
      teacherId,
      sourceNotificationId.slice('external:'.length),
      'CLICKED',
    );
  }

  recordSourceRead(
    bindingId: string,
    projectionType: 'IDENTITY' | 'METRICS' | 'COURSES' | 'G01_REVIEW',
    success: boolean,
    errorCode: string | null = null,
  ): Promise<unknown> {
    return this.database.queryTide(
      `
        INSERT INTO tide.source_read_status (
          id, teacher_binding_id, source_system, projection_type,
          last_success_at, last_failure_at, consecutive_failures,
          last_error_code
        ) VALUES (
          $1, $2, 'SHIWEN', $3,
          CASE WHEN $4 THEN now() ELSE NULL END,
          CASE WHEN $4 THEN NULL ELSE now() END,
          CASE WHEN $4 THEN 0 ELSE 1 END,
          $5
        )
        ON CONFLICT (teacher_binding_id, source_system, projection_type)
        DO UPDATE SET
          last_success_at = CASE WHEN $4 THEN now() ELSE tide.source_read_status.last_success_at END,
          last_failure_at = CASE WHEN $4 THEN tide.source_read_status.last_failure_at ELSE now() END,
          consecutive_failures = CASE WHEN $4 THEN 0 ELSE tide.source_read_status.consecutive_failures + 1 END,
          last_error_code = $5,
          updated_at = now()
        WHERE
          $4 = false
          OR tide.source_read_status.last_success_at IS NULL
          OR tide.source_read_status.last_success_at < now() - interval '5 minutes'
          OR tide.source_read_status.consecutive_failures > 0
          OR tide.source_read_status.last_error_code IS NOT NULL
      `,
      [randomUUID(), bindingId, projectionType, success, errorCode],
    );
  }

  private markExternalNotification(
    teacherId: string,
    notificationId: string,
    event: 'READ' | 'CLICKED',
  ): Promise<boolean> {
    return this.database.withTideTransaction(async (client) => {
      const notification = await client.query<{ notificationId: string }>(
        `
          SELECT notification_id AS "notificationId"
          FROM public.notifications
          WHERE notification_id = $1 AND teacher_id = $2
            AND status NOT IN ('CANCELLED', 'REVOKED')
            AND (
              $3::boolean = false
              OR (
                task_id IS NOT NULL
                AND status <> 'EXPIRED'
                AND (response_due_at IS NULL OR response_due_at > now())
                AND EXISTS (
                  SELECT 1
                  FROM public.task_assignments assignment
                  WHERE assignment.assignment_id = task_id
                    AND assignment.teacher_id = public.notifications.teacher_id
                    AND assignment.status NOT IN ('CANCELLED', 'EXPIRED')
                )
              )
            )
          FOR UPDATE
        `,
        [notificationId, teacherId, event === 'CLICKED'],
      );
      if (!notification.rows[0]) return false;

      const timestampColumn = event === 'READ' ? 'read_at' : 'clicked_at';
      await client.query(
        `
          UPDATE public.notifications
          SET ${timestampColumn} = COALESCE(${timestampColumn}, now()),
              status = CASE
                WHEN status IN ('CANCELLED', 'REVOKED', 'EXPIRED') THEN status
                ELSE $3::varchar
              END
          WHERE notification_id = $1 AND teacher_id = $2
        `,
        [notificationId, teacherId, event],
      );

      const requestHash = createHash('sha256')
        .update(`${notificationId}:${event}`)
        .digest('hex');
      await client.query(
        `
          INSERT INTO public.notification_events (
            notification_event_id, notification_id, delivery_status,
            occurred_at, request_hash, payload
          )
          VALUES (
            $1::varchar, $2::varchar, $3::varchar, now(), $4::varchar,
            jsonb_build_object('actor', 'TEACHER_APP')
          )
          ON CONFLICT (notification_id, request_hash) DO NOTHING
        `,
        [randomUUID(), notificationId, event, requestHash],
      );
      return true;
    });
  }

  private markSystemNotification(
    teacherId: string,
    notificationId: string,
    event: 'READ' | 'CLICKED',
  ): Promise<boolean> {
    return this.database.withTideTransaction(async (client) => {
      const column = event === 'READ' ? 'read_at' : 'clicked_at';
      const actionCondition =
        event === 'READ'
          ? ''
          : `
            AND action_type IS NOT NULL
            AND (expires_at IS NULL OR expires_at > now())
          `;
      const result = await client.query(
        `
          UPDATE tide.system_notifications
          SET ${column} = COALESCE(${column}, now()), updated_at = now()
          WHERE system_notification_id::text = $1
            AND teacher_id = $2
            AND cancelled_at IS NULL
            ${actionCondition}
        `,
        [notificationId, teacherId],
      );
      return (result.rowCount ?? 0) === 1;
    });
  }
}
