import { Injectable } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import type { QueryResultRow } from 'pg';
import { ZodError, type ZodType } from 'zod';
import type { AppEnvironment } from '../../platform/config/environment';
import { DatabaseService } from '../../platform/database/database.service';
import { quoteQualifiedViewName } from './qualified-view-name';
import { ShiwenTeacherReadAdapter } from './shiwen-read.adapters';
import {
  ShiwenContractError,
  ShiwenReadError,
  ShiwenSourceUnavailableError,
  ShiwenViewNotConfiguredError,
} from './shiwen-read.errors';
import type {
  ShiwenLessonScore,
  ShiwenTeacherIdentity,
  ShiwenTeacherScorecard,
} from './shiwen-read.models';
import {
  lessonScoreSchema,
  teacherIdentitySchema,
  teacherScorecardSchema,
} from './shiwen-read.schemas';

type ViewConfigurationKey = 'SHIWEN_TEACHER_IDENTITY_VIEW';

@Injectable()
export class PostgresShiwenReadAdapter implements ShiwenTeacherReadAdapter {
  constructor(
    private readonly config: ConfigService<AppEnvironment, true>,
    private readonly database: DatabaseService,
  ) {}

  async findIdentity(teacherId: string): Promise<ShiwenTeacherIdentity | null> {
    if (this.usesDirectTables()) {
      let identity: ShiwenTeacherIdentity | null;
      try {
        identity = await this.queryDirectOne(
          'findIdentity',
          teacherIdentitySchema,
          `
          SELECT
            teacher_id AS "teacherId",
            camp_enrollment_id AS "campEnrollmentId",
            name,
            timezone,
            camp_day AS "campDay",
            graduation_state AS "graduationState",
            data_mode AS "dataMode",
            updated_at AS "sourceUpdatedAt"
          FROM public.teachers
          WHERE teacher_id = $1
          LIMIT 1
        `,
          teacherId,
        );
      } catch (error) {
        if (!this.canUseFixtureAfterSourceError(error)) throw error;
        return this.findTideIdentityFixture(teacherId);
      }

      if (identity || !this.allowsTideFixtureFallback()) return identity;
      return this.findTideIdentityFixture(teacherId);
    }

    return this.queryOne(
      'findIdentity',
      'SHIWEN_TEACHER_IDENTITY_VIEW',
      teacherIdentitySchema,
      `
        SELECT
          teacher_id AS "teacherId",
          camp_enrollment_id AS "campEnrollmentId",
          name,
          timezone,
          camp_day AS "campDay",
          graduation_state AS "graduationState",
          data_mode AS "dataMode",
          source_updated_at AS "sourceUpdatedAt"
        FROM %VIEW%
        WHERE teacher_id = $1
        LIMIT 1
      `,
      teacherId,
    );
  }

  findScorecard(teacherId: string): Promise<ShiwenTeacherScorecard | null> {
    return this.queryDirectOne(
      'findScorecard',
      teacherScorecardSchema,
      `
        SELECT
          teacher_id AS "teacherId",
          camp_enrollment_id AS "campEnrollmentId",
          raw_total_score AS "rawTotalScore",
          public_total_score AS "publicTotalScore",
          graduation_state AS "graduationState",
          graduation_qualified AS "graduationQualified",
          gold_qualified AS "goldQualified",
          graduation_threshold AS "graduationThreshold",
          gold_threshold AS "goldThreshold",
          mandatory_task_completed_count AS "mandatoryTaskCompletedCount",
          mandatory_task_total_count AS "mandatoryTaskTotalCount",
          score_rule_version AS "scoreRuleVersion",
          calculated_at AS "calculatedAt",
          dimensions
        FROM public.teacher_scorecard_current
        WHERE teacher_id = $1
        LIMIT 1
      `,
      teacherId,
    );
  }

  listLessonScores(
    teacherId: string,
    options: { limit: number; offset: number; search?: string | null },
  ): Promise<ShiwenLessonScore[]> {
    const search = options.search?.trim() || null;
    return this.queryDirectMany(
      'listLessonScores',
      lessonScoreSchema,
      `
        SELECT
          teacher_id AS "teacherId",
          lesson_id AS "lessonId",
          lesson_sequence AS "lessonSequence",
          count(*) OVER() AS "lessonCount",
          source_appoint_id AS "sourceAppointId",
          scheduled_start_at AS "scheduledStartAt",
          lesson_local_date::text AS "lessonLocalDate",
          lesson_local_time::text AS "lessonLocalTime",
          lesson_lifecycle_status AS "lifecycleStatus",
          valid_for_scoring AS "validForScoring",
          evidence_status AS "evidenceStatus",
          lesson_total_score AS "lessonTotalScore",
          score_rule_version AS "scoreRuleVersion",
          updated_at AS "updatedAt",
          business_facts AS facts,
          dimensions
        FROM public.teacher_lesson_score_current
        WHERE teacher_id = $1
          AND (
            $4::text IS NULL
            OR lesson_id ILIKE '%' || $4 || '%'
            OR source_appoint_id ILIKE '%' || $4 || '%'
            OR lesson_sequence::text ILIKE '%' || $4 || '%'
            OR lesson_local_date::text ILIKE '%' || $4 || '%'
            OR lesson_local_time::text ILIKE '%' || $4 || '%'
          )
        ORDER BY lesson_sequence
        LIMIT $2 OFFSET $3
      `,
      [teacherId, options.limit, options.offset, search],
    );
  }

  private async queryOne<Result>(
    operation: string,
    viewKey: ViewConfigurationKey,
    schema: ZodType<Result>,
    sqlTemplate: string,
    teacherId: string,
  ): Promise<Result | null> {
    const rows = await this.query(operation, viewKey, sqlTemplate, teacherId);

    if (rows.length === 0) {
      return null;
    }

    return this.parseRow(operation, schema, rows[0]);
  }

  private async queryDirectOne<Result>(
    operation: string,
    schema: ZodType<Result>,
    sql: string,
    teacherId: string,
  ): Promise<Result | null> {
    const rows = await this.queryDirect(operation, sql, [teacherId]);
    return rows.length === 0 ? null : this.parseRow(operation, schema, rows[0]);
  }

  private async queryDirectMany<Result>(
    operation: string,
    schema: ZodType<Result>,
    sql: string,
    values: readonly unknown[],
  ): Promise<Result[]> {
    const rows = await this.queryDirect(operation, sql, values);
    return rows.map((row) => this.parseRow(operation, schema, row));
  }

  private async queryTideOne<Result>(
    operation: string,
    schema: ZodType<Result>,
    sql: string,
    teacherId: string,
  ): Promise<Result | null> {
    try {
      const result = await this.database.queryTide(sql, [teacherId]);
      return result.rows.length === 0
        ? null
        : this.parseRow(operation, schema, result.rows[0]);
    } catch (error) {
      if (error instanceof ShiwenReadError) throw error;
      throw new ShiwenSourceUnavailableError(operation, error);
    }
  }

  private findTideIdentityFixture(
    teacherId: string,
  ): Promise<ShiwenTeacherIdentity | null> {
    return this.queryTideOne(
      'findIdentityFixture',
      teacherIdentitySchema,
      `
        SELECT
          teacher_id AS "teacherId",
          camp_enrollment_id AS "campEnrollmentId",
          name,
          timezone,
          camp_day AS "campDay",
          graduation_state AS "graduationState",
          data_mode AS "dataMode",
          updated_at AS "sourceUpdatedAt"
        FROM public.teachers
        WHERE teacher_id = $1
          AND payload ->> 'internalTest' = 'true'
        LIMIT 1
      `,
      teacherId,
    );
  }

  private async query(
    operation: string,
    viewKey: ViewConfigurationKey,
    sqlTemplate: string,
    teacherId: string,
  ): Promise<QueryResultRow[]> {
    try {
      const viewName = this.requireView(viewKey);
      const sql = sqlTemplate.replace('%VIEW%', viewName);
      const result = await this.database.queryShiwen(sql, [teacherId]);
      return result.rows;
    } catch (error) {
      if (error instanceof ShiwenReadError) {
        throw error;
      }

      throw new ShiwenSourceUnavailableError(operation, error);
    }
  }

  private async queryDirect(
    operation: string,
    sql: string,
    values: readonly unknown[],
  ): Promise<QueryResultRow[]> {
    try {
      const result = await this.database.queryShiwen(sql, values);
      return result.rows;
    } catch (error) {
      if (error instanceof ShiwenReadError) throw error;
      throw new ShiwenSourceUnavailableError(operation, error);
    }
  }

  private parseRow<Result>(
    operation: string,
    schema: ZodType<Result>,
    row: QueryResultRow,
  ): Result {
    try {
      return schema.parse(row);
    } catch (error) {
      if (error instanceof ZodError) {
        throw new ShiwenContractError(operation, error);
      }

      throw error;
    }
  }

  private requireView(key: ViewConfigurationKey): string {
    const value = this.config.get(key, { infer: true });

    if (!value) {
      throw new ShiwenViewNotConfiguredError(key);
    }

    return quoteQualifiedViewName(value);
  }

  private usesDirectTables(): boolean {
    return (
      this.config.get('SHIWEN_READ_MODE', { infer: true }) === 'DIRECT_TABLES'
    );
  }

  private allowsTideFixtureFallback(): boolean {
    return this.config.get('SHIWEN_ALLOW_TIDE_FIXTURE_FALLBACK', {
      infer: true,
    });
  }

  private canUseFixtureAfterSourceError(error: unknown): boolean {
    return (
      this.allowsTideFixtureFallback() &&
      error instanceof ShiwenSourceUnavailableError
    );
  }
}
