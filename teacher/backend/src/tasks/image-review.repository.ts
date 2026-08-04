import { Injectable } from '@nestjs/common';
import { randomUUID } from 'node:crypto';
import type { PoolClient, QueryResultRow } from 'pg';
import type { FileStorageProvider } from '../files/file-storage.adapter';

interface ReviewFileRow extends QueryResultRow {
  fileId: string;
  storageProvider: FileStorageProvider;
  objectKey: string;
  originalFilename: string;
  mimeType: string;
}

export interface ImageReviewItem {
  criterionKey: string;
  result: 'PASS' | 'FAIL' | 'UNKNOWN';
  teacherMessage: string | null;
}

@Injectable()
export class ImageReviewRepository {
  async findSubmissionFile(
    client: PoolClient,
    input: {
      accountId: string;
      taskInstanceId: string;
      submissionId: string;
      stepKey: string;
      fileId: string;
    },
  ): Promise<ReviewFileRow | null> {
    const result = await client.query<ReviewFileRow>(
      `
        SELECT
          file.id AS "fileId", file.storage_provider AS "storageProvider",
          file.object_key AS "objectKey",
          file.original_filename AS "originalFilename", file.mime_type AS "mimeType"
        FROM tide.task_submission_files link
        JOIN tide.task_submissions submission ON submission.id = link.submission_id
        JOIN public.task_assignments task ON task.assignment_id = submission.task_assignment_id
        JOIN tide.teacher_bindings binding ON binding.teacher_id = task.teacher_id
        JOIN tide.file_objects file ON file.id = link.file_id
        WHERE link.submission_id = $1
          AND link.file_id = $2
          AND link.purpose = $3
          AND task.assignment_id = $4
          AND binding.account_id = $5
          AND binding.status = 'ACTIVE'
          AND file.status = 'READY'
        LIMIT 1
      `,
      [
        input.submissionId,
        input.fileId,
        input.stepKey,
        input.taskInstanceId,
        input.accountId,
      ],
    );
    return result.rows[0] ?? null;
  }

  async isActivePromptVersion(
    client: PoolClient,
    promptVersionId: string,
  ): Promise<boolean> {
    const result = await client.query(
      `
        SELECT 1
        FROM tide.ai_prompt_versions
        WHERE id = $1 AND capability = 'TASK_IMAGE_REVIEW' AND status = 'ACTIVE'
        LIMIT 1
      `,
      [promptVersionId],
    );
    return (result.rowCount ?? 0) > 0;
  }

  async hasPassedReview(
    client: PoolClient,
    input: { fileId: string; criteriaVersion: string },
  ): Promise<boolean> {
    const result = await client.query(
      `
        SELECT 1
        FROM tide.image_reviews
        WHERE file_id = $1
          AND criteria_version = $2
          AND decision = 'PASS'
        LIMIT 1
      `,
      [input.fileId, input.criteriaVersion],
    );
    return (result.rowCount ?? 0) > 0;
  }

  async save(
    client: PoolClient,
    input: {
      fileId: string;
      submissionId: string;
      aiRunId: string | null;
      criteriaVersion: string;
      decision: 'PASS' | 'RETRY' | 'ERROR';
      teacherReason: string;
      confidenceSummary: Record<string, unknown>;
      items: ImageReviewItem[];
    },
  ): Promise<void> {
    const reviewId = randomUUID();
    await client.query(
      `
        INSERT INTO tide.image_reviews (
          id, file_id, submission_id, ai_run_id, criteria_version,
          decision, teacher_reason, confidence_summary
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
      `,
      [
        reviewId,
        input.fileId,
        input.submissionId,
        input.aiRunId,
        input.criteriaVersion,
        input.decision,
        input.teacherReason,
        input.confidenceSummary,
      ],
    );
    for (const item of input.items) {
      await client.query(
        `
          INSERT INTO tide.image_review_items (
            id, image_review_id, criterion_key, result, teacher_message
          ) VALUES ($1, $2, $3, $4, $5)
        `,
        [
          randomUUID(),
          reviewId,
          item.criterionKey,
          item.result,
          item.teacherMessage,
        ],
      );
    }
  }
}
