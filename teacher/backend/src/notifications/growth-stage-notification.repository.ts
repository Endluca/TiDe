import { Injectable } from '@nestjs/common';
import type { QueryResultRow } from 'pg';
import { DatabaseService } from '../platform/database/database.service';

interface GrowthStageScanRow extends QueryResultRow {
  scannedTeachers: number | string;
  initializedTeachers: number | string;
  createdNotifications: number | string;
}

export interface GrowthStageNotificationScanResult {
  scannedTeachers: number;
  initializedTeachers: number;
  createdNotifications: number;
}

@Injectable()
export class GrowthStageNotificationRepository {
  constructor(private readonly database: DatabaseService) {}

  async scanAndCreate(
    now: Date,
    batchSize = 200,
  ): Promise<GrowthStageNotificationScanResult> {
    const result = await this.database.queryTide<GrowthStageScanRow>(
      `
        WITH teacher_progress AS (
          SELECT
            binding.teacher_id,
            teacher.camp_day,
            count(DISTINCT assignment.task_code) FILTER (
              WHERE assignment.task_code IN ('G01', 'G02', 'G03', 'G04')
            ) AS stage_one_tasks,
            count(DISTINCT assignment.task_code) FILTER (
              WHERE assignment.task_code IN ('G05', 'G06', 'G07')
            ) AS stage_two_tasks,
            count(DISTINCT assignment.task_code) FILTER (
              WHERE assignment.task_code IN ('G08', 'G09')
            ) AS stage_three_tasks,
            count(DISTINCT assignment.task_code) FILTER (
              WHERE assignment.task_code IN ('G01', 'G02', 'G03', 'G04')
                AND assignment.status = 'COMPLETED'
            ) AS stage_one_completed,
            count(DISTINCT assignment.task_code) FILTER (
              WHERE assignment.task_code IN ('G05', 'G06', 'G07')
                AND assignment.status = 'COMPLETED'
            ) AS stage_two_completed
          FROM tide.teacher_bindings binding
          JOIN public.teachers teacher
            ON teacher.teacher_id = binding.teacher_id
          LEFT JOIN public.task_assignments assignment
            ON assignment.teacher_id = binding.teacher_id
           AND assignment.task_kind = 'FIXED_GROWTH'
          WHERE binding.status = 'ACTIVE'
          GROUP BY binding.teacher_id, teacher.camp_day
        ),
        available_stages AS (
          SELECT
            teacher_id,
            CASE
              WHEN stage_three_tasks = 2
               AND (camp_day >= 15 OR stage_two_completed = 3) THEN 3
              WHEN stage_two_tasks = 3
               AND (camp_day >= 8 OR stage_one_completed = 4) THEN 2
              ELSE 1
            END::smallint AS highest_available_stage
          FROM teacher_progress
          WHERE stage_one_tasks = 4
        ),
        candidates AS (
          SELECT
            available.teacher_id,
            available.highest_available_stage,
            state.highest_available_stage AS previous_stage
          FROM available_stages available
          LEFT JOIN tide.growth_stage_notification_states state
            ON state.teacher_id = available.teacher_id
          WHERE state.teacher_id IS NULL
             OR available.highest_available_stage > state.highest_available_stage
          ORDER BY available.teacher_id
          LIMIT $2
        ),
        upserted AS (
          INSERT INTO tide.growth_stage_notification_states (
            teacher_id, highest_available_stage, initialized_at, updated_at
          )
          SELECT teacher_id, highest_available_stage, $1, $1
          FROM candidates
          ON CONFLICT (teacher_id) DO UPDATE
          SET highest_available_stage = EXCLUDED.highest_available_stage,
              updated_at = EXCLUDED.updated_at
          WHERE tide.growth_stage_notification_states.highest_available_stage
              < EXCLUDED.highest_available_stage
          RETURNING teacher_id, highest_available_stage
        ),
        created AS (
          INSERT INTO tide.system_notifications (
            system_notification_id, teacher_id, type_code, title, body,
            action_type, action_target, dedupe_key, payload, issued_at
          )
          SELECT
            gen_random_uuid(),
            upserted.teacher_id,
            'GROWTH_STAGE_AVAILABLE',
            'Your next growth stage is ready',
            'A new set of required tasks is now available in your growth path. Complete them in the order that works best for you.',
            'TASKS',
            '/path',
            'growth-stage-available:' || upserted.teacher_id || ':' ||
              upserted.highest_available_stage::text,
            jsonb_build_object(
              'stageNumber', upserted.highest_available_stage
            ),
            $1
          FROM upserted
          JOIN candidates
            ON candidates.teacher_id = upserted.teacher_id
          WHERE candidates.previous_stage IS NOT NULL
          ON CONFLICT (dedupe_key) DO NOTHING
          RETURNING 1
        )
        SELECT
          (SELECT count(*)::integer FROM candidates) AS "scannedTeachers",
          (
            SELECT count(*)::integer
            FROM candidates
            WHERE previous_stage IS NULL
          ) AS "initializedTeachers",
          (SELECT count(*)::integer FROM created) AS "createdNotifications"
      `,
      [now, batchSize],
    );
    const row = result.rows[0];
    return {
      scannedTeachers: Number(row?.scannedTeachers ?? 0),
      initializedTeachers: Number(row?.initializedTeachers ?? 0),
      createdNotifications: Number(row?.createdNotifications ?? 0),
    };
  }
}
