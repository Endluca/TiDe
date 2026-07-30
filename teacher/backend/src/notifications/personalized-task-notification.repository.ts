import { Injectable } from '@nestjs/common';
import { randomUUID } from 'node:crypto';
import type { PoolClient, QueryResultRow } from 'pg';
import { DatabaseService } from '../platform/database/database.service';
import {
  buildPersonalizedTaskReminders,
  PERSONALIZED_TASK_REMINDER_STATUSES,
  type PersonalizedTaskReminderAssignment,
} from './personalized-task-notification.policy';

interface PersonalizedTaskReminderRow extends QueryResultRow {
  assignmentId: string;
  teacherId: string;
  taskCode: string;
  displayTitle: string | null;
  status: string;
  assignedAt: Date;
  dueAt: Date | null;
  timezoneUsed: string | null;
  timezoneSource: string | null;
  timezoneVerifiedAt: Date | null;
}

export interface PersonalizedTaskReminderScanResult {
  scannedAssignments: number;
  createdNotifications: number;
}

@Injectable()
export class PersonalizedTaskNotificationRepository {
  constructor(private readonly database: DatabaseService) {}

  scanAndCreate(
    now: Date,
    rolloutAt: Date,
    batchSize = 200,
  ): Promise<PersonalizedTaskReminderScanResult> {
    return this.database.withTideTransaction(async (client) => {
      const assignments = await this.findCandidates(
        client,
        now,
        rolloutAt,
        batchSize,
      );
      const reminders = assignments.flatMap((assignment) =>
        buildPersonalizedTaskReminders(assignment, now, rolloutAt),
      );
      const inserted =
        reminders.length === 0
          ? { rowCount: 0 }
          : await client.query(
              `
                INSERT INTO tide.system_notifications (
                  system_notification_id, teacher_id, type_code, title, body,
                  action_type, action_target, expires_at, dedupe_key, payload,
                  issued_at
                )
                SELECT
                  input.notification_id,
                  input.teacher_id,
                  input.type_code,
                  input.title,
                  input.body,
                  'TASK_DETAIL',
                  input.action_target,
                  input.expires_at,
                  input.dedupe_key,
                  jsonb_build_object(
                    'taskAssignmentId', input.assignment_id,
                    'reminderType', input.reminder_type
                  ),
                  $11
                FROM unnest(
                  $1::uuid[],
                  $2::varchar[],
                  $3::text[],
                  $4::text[],
                  $5::text[],
                  $6::text[],
                  $7::timestamptz[],
                  $8::text[],
                  $9::text[],
                  $10::text[]
                ) AS input(
                  notification_id,
                  teacher_id,
                  type_code,
                  title,
                  body,
                  action_target,
                  expires_at,
                  dedupe_key,
                  assignment_id,
                  reminder_type
                )
                ON CONFLICT (dedupe_key) DO NOTHING
              `,
              [
                reminders.map(() => randomUUID()),
                reminders.map((reminder) => reminder.teacherId),
                reminders.map((reminder) => reminder.typeCode),
                reminders.map((reminder) => reminder.title),
                reminders.map((reminder) => reminder.body),
                reminders.map((reminder) => reminder.actionTarget),
                reminders.map((reminder) => reminder.expiresAt),
                reminders.map((reminder) => reminder.dedupeKey),
                reminders.map((reminder) => reminder.assignmentId),
                reminders.map((reminder) => reminder.reminderType),
                now,
              ],
            );

      return {
        scannedAssignments: assignments.length,
        createdNotifications: inserted.rowCount ?? 0,
      };
    });
  }

  private async findCandidates(
    client: PoolClient,
    now: Date,
    rolloutAt: Date,
    batchSize: number,
  ): Promise<PersonalizedTaskReminderAssignment[]> {
    const dueWindowEnd = new Date(now.getTime() + 24 * 60 * 60 * 1000);
    const result = await client.query<PersonalizedTaskReminderRow>(
      `
        SELECT
          assignment.assignment_id AS "assignmentId",
          assignment.teacher_id AS "teacherId",
          assignment.task_code AS "taskCode",
          assignment.display_title AS "displayTitle",
          assignment.status,
          assignment.assigned_at AS "assignedAt",
          assignment.due_at AS "dueAt",
          assignment.timezone_used AS "timezoneUsed",
          assignment.timezone_source AS "timezoneSource",
          assignment.timezone_verified_at AS "timezoneVerifiedAt"
        FROM public.task_assignments assignment
        WHERE assignment.task_kind = 'PERSONALIZED_IMPROVEMENT'
          AND assignment.creator_system = 'TRIGGER_CENTER'
          AND (
            (
              assignment.assigned_at >= $1
              AND assignment.assigned_at <= $2
              AND NOT EXISTS (
                SELECT 1
                FROM tide.system_notifications existing_assigned
                WHERE existing_assigned.dedupe_key =
                  'personalized-task-assigned:' || assignment.assignment_id
              )
            )
            OR (
              assignment.due_at > $2
              AND assignment.due_at <= $3
              AND assignment.status = ANY($4::varchar[])
              AND NOT EXISTS (
                SELECT 1
                FROM tide.system_notifications existing_due
                WHERE existing_due.dedupe_key =
                  'personalized-task-due-24h:' || assignment.assignment_id
              )
            )
          )
        ORDER BY assignment.assigned_at, assignment.assignment_id
        LIMIT $5
      `,
      [
        rolloutAt,
        now,
        dueWindowEnd,
        PERSONALIZED_TASK_REMINDER_STATUSES,
        batchSize,
      ],
    );
    return result.rows;
  }
}
