import { Injectable } from '@nestjs/common';
import { randomUUID } from 'node:crypto';
import type { QueryResultRow } from 'pg';
import type { FileStorageProvider } from '../files/file-storage.adapter';
import { DatabaseService } from '../platform/database/database.service';
import type {
  TeacherPhotoCheck,
  TeacherPhotoDecision,
  TeacherPhotoRunRecord,
  TeacherPhotoRunStatus,
} from './teacher-photo.models';

interface TeacherPhotoRow extends QueryResultRow {
  photoRunId: string;
  taskInstanceId: string;
  accountId: string;
  originalFileId: string;
  originalStorageProvider: FileStorageProvider;
  originalObjectKey: string;
  originalFilename: string;
  originalMimeType: string;
  finalFileId: string | null;
  finalStorageProvider: FileStorageProvider | null;
  finalObjectKey: string | null;
  status: TeacherPhotoRunStatus;
  decision: TeacherPhotoDecision | null;
  criteriaVersion: string;
  teacherMessage: string | null;
  checks: TeacherPhotoCheck[];
  filterPreset: string | null;
  filterStrength: string | null;
  submittedAt: Date;
  checkedAt: Date | null;
  processedAt: Date | null;
  requestHash: string;
  processingOwner: string | null;
  leaseExpiresAt: Date | null;
  attemptCount: number;
  nextAttemptAt: Date | null;
}

export type ReserveTeacherPhotoResult =
  | { type: 'CREATED'; run: TeacherPhotoRunRecord }
  | { type: 'REPLAY' | 'LOCKED'; run: TeacherPhotoRunRecord }
  | { type: 'IDEMPOTENCY_CONFLICT' }
  | { type: 'TASK_NOT_FOUND' };

@Injectable()
export class TeacherPhotoRepository {
  constructor(private readonly database: DatabaseService) {}

  reserve(input: {
    accountId: string;
    taskInstanceId: string;
    idempotencyKey: string;
    requestHash: string;
    originalFileId: string;
    storageProvider: FileStorageProvider;
    originalObjectKey: string;
    originalFilename: string;
    mimeType: string;
    sizeBytes: number;
    sha256: string;
    criteriaVersion: string;
  }): Promise<ReserveTeacherPhotoResult> {
    return this.database.withTideTransaction(async (client) => {
      await client.query(
        `SELECT pg_advisory_xact_lock(hashtextextended($1, 0))`,
        [`teacher-photo:${input.taskInstanceId}`],
      );
      const task = await client.query(
        `
          SELECT 1
          FROM public.task_assignments task
          JOIN tide.teacher_bindings binding ON binding.teacher_id = task.teacher_id
          JOIN tide.task_execution_versions execution
            ON execution.shared_template_row_id = task.template_version_id
           AND execution.status = 'ACTIVE'
          JOIN tide.task_step_definitions step
            ON step.execution_version_id = execution.id
           AND step.step_key = 'readiness-photo'
           AND step.step_type = 'UPLOAD'
          WHERE task.assignment_id = $1
            AND task.task_code = 'G00'
            AND task.status IN ('ASSIGNED', 'VIEWED', 'IN_PROGRESS', 'FAILED', 'COMPLETED')
            AND binding.account_id = $2
            AND binding.status = 'ACTIVE'
          LIMIT 1
        `,
        [input.taskInstanceId, input.accountId],
      );
      if (task.rowCount === 0) return { type: 'TASK_NOT_FOUND' };

      await client.query(
        `
          UPDATE tide.file_objects
          SET status = 'QUARANTINED'
          WHERE status = 'PENDING'
            AND id IN (
              SELECT original_file_id
              FROM tide.teacher_photo_runs
              WHERE task_assignment_id = $1
                AND status = 'UPLOADING'
                AND submitted_at < now() - interval '2 minutes'
            )
        `,
        [input.taskInstanceId],
      );
      await client.query(
        `
          UPDATE tide.teacher_photo_runs
          SET status = CASE
                WHEN status = 'BEAUTIFYING' THEN 'PROCESSING_FAILED'
                ELSE 'UNDER_REVIEW'
              END,
              decision = CASE
                WHEN status = 'BEAUTIFYING' THEN decision
                ELSE 'ERROR'
              END,
              teacher_message = CASE
                WHEN status = 'BEAUTIFYING'
                  THEN '检测结果保存超时，请重新检测。'
                ELSE '上一次自动检测未正常结束，请重新检测。'
              END,
              error_code = CASE
                WHEN status = 'BEAUTIFYING'
                  THEN 'TEACHER_PHOTO_BEAUTIFYING_TIMEOUT'
                ELSE 'TEACHER_PHOTO_CHECK_TIMEOUT'
              END,
              checked_at = COALESCE(checked_at, now())
          WHERE task_assignment_id = $1
            AND status IN ('UPLOADING', 'CHECKING', 'BEAUTIFYING')
            AND submitted_at < now() - interval '2 minutes'
        `,
        [input.taskInstanceId],
      );

      const existingKey = await client.query<TeacherPhotoRow>(
        `${this.selectRun()} WHERE run.account_id = $1 AND run.idempotency_key = $2 LIMIT 1`,
        [input.accountId, input.idempotencyKey],
      );
      if (existingKey.rows[0]) {
        return existingKey.rows[0].requestHash === input.requestHash
          ? { type: 'REPLAY', run: this.toRecord(existingKey.rows[0]) }
          : { type: 'IDEMPOTENCY_CONFLICT' };
      }

      const locked = await client.query<TeacherPhotoRow>(
        `${this.selectRun()} WHERE run.task_assignment_id = $1 AND run.status IN ('UPLOADING', 'CHECKING', 'BEAUTIFYING', 'READY') ORDER BY run.submitted_at DESC LIMIT 1`,
        [input.taskInstanceId],
      );
      if (locked.rows[0])
        return { type: 'LOCKED', run: this.toRecord(locked.rows[0]) };

      await client.query(
        `
          INSERT INTO tide.file_objects (
            id, uploader_account_id, storage_provider, object_key,
            original_filename, mime_type, size_bytes, sha256, visibility, status
          ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'PRIVATE', 'PENDING')
        `,
        [
          input.originalFileId,
          input.accountId,
          input.storageProvider,
          input.originalObjectKey,
          input.originalFilename,
          input.mimeType,
          input.sizeBytes,
          input.sha256,
        ],
      );
      const photoRunId = randomUUID();
      const inserted = await client.query<TeacherPhotoRow>(
        `
          INSERT INTO tide.teacher_photo_runs (
            id, task_assignment_id, account_id, original_file_id,
            idempotency_key, request_hash, status, criteria_version
          ) VALUES ($1, $2, $3, $4, $5, $6, 'UPLOADING', $7)
          RETURNING id AS "photoRunId", task_assignment_id AS "taskInstanceId",
            account_id AS "accountId", original_file_id AS "originalFileId",
            NULL::uuid AS "finalFileId", status, decision,
            criteria_version AS "criteriaVersion", teacher_message AS "teacherMessage",
            checks, filter_preset AS "filterPreset", filter_strength AS "filterStrength",
            submitted_at AS "submittedAt", checked_at AS "checkedAt",
            processed_at AS "processedAt", request_hash AS "requestHash",
            $8::text AS "originalObjectKey",
            $9::text AS "originalStorageProvider",
            $10::text AS "originalFilename",
            $11::text AS "originalMimeType",
            NULL::text AS "finalObjectKey",
            NULL::text AS "finalStorageProvider"
        `,
        [
          photoRunId,
          input.taskInstanceId,
          input.accountId,
          input.originalFileId,
          input.idempotencyKey,
          input.requestHash,
          input.criteriaVersion,
          input.originalObjectKey,
          input.storageProvider,
          input.originalFilename,
          input.mimeType,
        ],
      );
      return { type: 'CREATED', run: this.toRecord(inserted.rows[0]) };
    });
  }

  markOriginalReady(photoRunId: string, originalFileId: string): Promise<void> {
    return this.database.withTideTransaction(async (client) => {
      await client.query(
        `UPDATE tide.file_objects SET status = 'READY', ready_at = now() WHERE id = $1 AND status = 'PENDING'`,
        [originalFileId],
      );
      await client.query(
        `UPDATE tide.teacher_photo_runs SET status = 'CHECKING' WHERE id = $1 AND status = 'UPLOADING'`,
        [photoRunId],
      );
    });
  }

  markUploadFailed(photoRunId: string, originalFileId: string): Promise<void> {
    return this.database.withTideTransaction(async (client) => {
      await client.query(
        `UPDATE tide.file_objects SET status = 'QUARANTINED' WHERE id = $1`,
        [originalFileId],
      );
      await client.query(
        `UPDATE tide.teacher_photo_runs SET status = 'PROCESSING_FAILED', error_code = 'ORIGINAL_STORAGE_FAILED' WHERE id = $1`,
        [photoRunId],
      );
    });
  }

  review(input: {
    photoRunId: string;
    processingOwner: string;
    aiRunId: string | null;
    decision: TeacherPhotoDecision;
    teacherMessage: string;
    checks: TeacherPhotoCheck[];
    confidenceSummary: Record<string, unknown>;
  }): Promise<boolean> {
    return this.database.withTideTransaction(async (client) => {
      const result = await client.query(
        `
          UPDATE tide.teacher_photo_runs
          SET ai_run_id = $2, decision = $3, teacher_message = $4,
              checks = $5::jsonb, confidence_summary = $6::jsonb,
              checked_at = now(),
              status = CASE $3::text WHEN 'PASS' THEN 'BEAUTIFYING'
                       WHEN 'RETRY' THEN 'RETRY_REQUIRED' ELSE 'UNDER_REVIEW' END,
              processing_owner = CASE
                WHEN $3::text = 'PASS' THEN processing_owner
                ELSE NULL
              END,
              lease_expires_at = CASE
                WHEN $3::text = 'PASS' THEN lease_expires_at
                ELSE NULL
              END
          WHERE id = $1 AND status = 'CHECKING'
            AND processing_owner = $7
            AND lease_expires_at > now()
        `,
        [
          input.photoRunId,
          input.aiRunId,
          input.decision,
          input.teacherMessage,
          JSON.stringify(input.checks),
          JSON.stringify(input.confidenceSummary),
          input.processingOwner,
        ],
      );
      return result.rowCount === 1;
    });
  }

  async reserveFinal(input: {
    photoRunId: string;
    processingOwner: string;
    accountId: string;
    fileId: string;
    objectKey: string;
    filename: string;
    mimeType: string;
    sizeBytes: number;
    sha256: string;
    storageProvider: FileStorageProvider;
    filterPreset: string;
    filterStrength: number;
    sourceMetrics: object;
  }): Promise<boolean> {
    return this.database.withTideTransaction(async (client) => {
      const owned = await client.query(
        `
          SELECT 1
          FROM tide.teacher_photo_runs
          WHERE id = $1 AND status = 'BEAUTIFYING'
            AND processing_owner = $2
            AND lease_expires_at > now()
          FOR UPDATE
        `,
        [input.photoRunId, input.processingOwner],
      );
      if (owned.rowCount !== 1) return false;

      await client.query(
        `
          INSERT INTO tide.file_objects (
            id, uploader_account_id, storage_provider, object_key,
            original_filename, mime_type, size_bytes, sha256, visibility, status
          ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'PRIVATE', 'PENDING')
          ON CONFLICT (id) DO UPDATE
          SET storage_provider = EXCLUDED.storage_provider,
              object_key = EXCLUDED.object_key,
              original_filename = EXCLUDED.original_filename,
              mime_type = EXCLUDED.mime_type,
              size_bytes = EXCLUDED.size_bytes,
              sha256 = EXCLUDED.sha256,
              status = 'PENDING',
              ready_at = NULL
        `,
        [
          input.fileId,
          input.accountId,
          input.storageProvider,
          input.objectKey,
          input.filename,
          input.mimeType,
          input.sizeBytes,
          input.sha256,
        ],
      );
      const result = await client.query(
        `
          UPDATE tide.teacher_photo_runs
          SET final_file_id = COALESCE(final_file_id, $2),
              filter_preset = $3, filter_strength = $4,
              source_metrics = $5
          WHERE id = $1 AND status = 'BEAUTIFYING'
            AND (final_file_id IS NULL OR final_file_id = $2)
            AND processing_owner = $6
            AND lease_expires_at > now()
        `,
        [
          input.photoRunId,
          input.fileId,
          input.filterPreset,
          input.filterStrength,
          input.sourceMetrics,
          input.processingOwner,
        ],
      );
      return result.rowCount === 1;
    });
  }

  async claimPending(
    processingOwner: string,
    limit: number,
    leaseMs: number,
  ): Promise<TeacherPhotoRunRecord[]> {
    return this.database.withTideTransaction(async (client) => {
      const claimed = await client.query<{ id: string }>(
        `
          WITH candidates AS (
            SELECT id
            FROM tide.teacher_photo_runs
            WHERE status IN ('CHECKING', 'BEAUTIFYING')
              AND next_attempt_at <= now()
              AND (
                processing_owner IS NULL
                OR lease_expires_at <= now()
              )
            ORDER BY
              CASE status WHEN 'BEAUTIFYING' THEN 0 ELSE 1 END,
              submitted_at,
              id
            FOR UPDATE SKIP LOCKED
            LIMIT $2
          )
          UPDATE tide.teacher_photo_runs run
          SET processing_owner = $1,
              lease_expires_at = now() + ($3 * interval '1 millisecond'),
              attempt_count = attempt_count + 1
          FROM candidates
          WHERE run.id = candidates.id
          RETURNING run.id
        `,
        [processingOwner, limit, leaseMs],
      );
      if (claimed.rows.length === 0) return [];
      const result = await client.query<TeacherPhotoRow>(
        `
          ${this.selectRun()}
          WHERE run.id = ANY($1::uuid[])
          ORDER BY run.submitted_at, run.id
        `,
        [claimed.rows.map((row) => row.id)],
      );
      return result.rows.map((row) => this.toRecord(row));
    });
  }

  markAttemptFailed(input: {
    photoRunId: string;
    processingOwner: string;
    attemptCount: number;
    stage: 'CHECKING' | 'BEAUTIFYING';
    errorCode: string;
    teacherMessage: string;
    retryDelayMs: number;
    fileId?: string;
  }): Promise<void> {
    return this.database.withTideTransaction(async (client) => {
      const result = await client.query(
        `
          UPDATE tide.teacher_photo_runs
          SET status = CASE
                WHEN $3 >= 3 THEN
                  CASE WHEN $4 = 'CHECKING' THEN 'UNDER_REVIEW'
                       ELSE 'PROCESSING_FAILED' END
                ELSE status
              END,
              decision = CASE
                WHEN $3 >= 3 AND $4 = 'CHECKING' THEN 'ERROR'
                ELSE decision
              END,
              teacher_message = CASE
                WHEN $3 >= 3 THEN $6
                ELSE teacher_message
              END,
              error_code = $5,
              checked_at = CASE
                WHEN $3 >= 3 AND $4 = 'CHECKING'
                  THEN COALESCE(checked_at, now())
                ELSE checked_at
              END,
              next_attempt_at = CASE
                WHEN $3 >= 3 THEN next_attempt_at
                ELSE now() + ($7 * interval '1 millisecond')
              END,
              processing_owner = NULL,
              lease_expires_at = NULL
          WHERE id = $1
            AND processing_owner = $2
        `,
        [
          input.photoRunId,
          input.processingOwner,
          input.attemptCount,
          input.stage,
          input.errorCode,
          input.teacherMessage,
          input.retryDelayMs,
        ],
      );
      if (result.rowCount === 1 && input.fileId) {
        await client.query(
          `UPDATE tide.file_objects SET status = 'QUARANTINED' WHERE id = $1`,
          [input.fileId],
        );
      }
    });
  }

  completeFinal(
    photoRunId: string,
    fileId: string,
    taskInstanceId: string,
    processingOwner: string,
  ): Promise<boolean> {
    return this.database.withTideTransaction(async (client) => {
      const result = await client.query(
        `UPDATE tide.teacher_photo_runs
         SET status = 'READY', processed_at = now(), error_code = NULL,
             processing_owner = NULL, lease_expires_at = NULL
         WHERE id = $1 AND final_file_id = $2
           AND processing_owner = $3
           AND lease_expires_at > now()`,
        [photoRunId, fileId, processingOwner],
      );
      if (result.rowCount !== 1) return false;
      await client.query(
        `UPDATE tide.file_objects SET status = 'READY', ready_at = now() WHERE id = $1 AND status = 'PENDING'`,
        [fileId],
      );
      await client.query(
        `
          INSERT INTO tide.task_step_progress (
            id, task_assignment_id, step_key, status, percent, progress_summary, completed_at
          ) VALUES ($1, $2, 'readiness-photo', 'COMPLETED', 100,
                    jsonb_build_object('photoRunId', $3::text, 'finalFileId', $4::text), now())
          ON CONFLICT (task_assignment_id, step_key) WHERE task_assignment_id IS NOT NULL
          DO UPDATE SET status = 'COMPLETED', percent = 100,
            progress_summary = EXCLUDED.progress_summary,
            completed_at = COALESCE(tide.task_step_progress.completed_at, now()),
            updated_at = now()
        `,
        [randomUUID(), taskInstanceId, photoRunId, fileId],
      );
      return true;
    });
  }

  async findOwned(
    accountId: string,
    taskInstanceId: string,
  ): Promise<TeacherPhotoRunRecord | null> {
    const result = await this.database.queryTide<TeacherPhotoRow>(
      `${this.selectRun()} WHERE run.account_id = $1 AND run.task_assignment_id = $2 ORDER BY run.submitted_at DESC LIMIT 1`,
      [accountId, taskInstanceId],
    );
    return result.rows[0] ? this.toRecord(result.rows[0]) : null;
  }

  async findReadyContent(
    accountId: string,
    taskInstanceId: string,
  ): Promise<{
    storageProvider: FileStorageProvider;
    objectKey: string;
    mimeType: string;
    filename: string;
  } | null> {
    const result = await this.database.queryTide<{
      storageProvider: FileStorageProvider;
      objectKey: string;
      mimeType: string;
      filename: string;
    }>(
      `
        SELECT file.storage_provider AS "storageProvider",
          file.object_key AS "objectKey", file.mime_type AS "mimeType",
          file.original_filename AS filename
        FROM tide.teacher_photo_runs run
        JOIN tide.file_objects file ON file.id = run.final_file_id
        WHERE run.account_id = $1 AND run.task_assignment_id = $2
          AND run.status = 'READY' AND file.status = 'READY'
        ORDER BY run.processed_at DESC LIMIT 1
      `,
      [accountId, taskInstanceId],
    );
    return result.rows[0] ?? null;
  }

  private selectRun(): string {
    return `
      SELECT run.id AS "photoRunId", run.task_assignment_id AS "taskInstanceId",
        run.account_id AS "accountId", run.original_file_id AS "originalFileId",
        original.storage_provider AS "originalStorageProvider",
        original.object_key AS "originalObjectKey",
        original.original_filename AS "originalFilename",
        original.mime_type AS "originalMimeType",
        run.final_file_id AS "finalFileId",
        final.storage_provider AS "finalStorageProvider",
        final.object_key AS "finalObjectKey", run.status, run.decision,
        run.criteria_version AS "criteriaVersion", run.teacher_message AS "teacherMessage",
        run.checks, run.filter_preset AS "filterPreset",
        run.filter_strength AS "filterStrength", run.submitted_at AS "submittedAt",
        run.checked_at AS "checkedAt", run.processed_at AS "processedAt",
        run.request_hash AS "requestHash",
        run.processing_owner AS "processingOwner",
        run.lease_expires_at AS "leaseExpiresAt",
        run.attempt_count AS "attemptCount",
        run.next_attempt_at AS "nextAttemptAt"
      FROM tide.teacher_photo_runs run
      JOIN tide.file_objects original ON original.id = run.original_file_id
      LEFT JOIN tide.file_objects final ON final.id = run.final_file_id
    `;
  }

  private toRecord(row: TeacherPhotoRow): TeacherPhotoRunRecord {
    return {
      photoRunId: row.photoRunId,
      taskInstanceId: row.taskInstanceId,
      accountId: row.accountId,
      originalFileId: row.originalFileId,
      originalStorageProvider: row.originalStorageProvider,
      originalObjectKey: row.originalObjectKey,
      originalFilename: row.originalFilename,
      originalMimeType: row.originalMimeType,
      finalFileId: row.finalFileId,
      finalStorageProvider: row.finalStorageProvider,
      finalObjectKey: row.finalObjectKey,
      status: row.status,
      decision: row.decision,
      criteriaVersion: row.criteriaVersion,
      teacherMessage: row.teacherMessage,
      checks: Array.isArray(row.checks) ? row.checks : [],
      filterPreset: row.filterPreset,
      filterStrength:
        row.filterStrength === null ? null : Number(row.filterStrength),
      submittedAt: row.submittedAt,
      checkedAt: row.checkedAt,
      processedAt: row.processedAt,
      processingOwner: row.processingOwner,
      leaseExpiresAt: row.leaseExpiresAt,
      attemptCount: Number(row.attemptCount),
      nextAttemptAt: row.nextAttemptAt,
    };
  }
}
