import { Injectable } from '@nestjs/common';
import { randomUUID } from 'node:crypto';
import type { PoolClient, QueryResultRow } from 'pg';
import { DatabaseService } from '../platform/database/database.service';
import type { FileStorageProvider } from './file-storage.adapter';
import type { FileObjectStatus, OwnedFileRecord } from './file.models';

interface UploadIntentRow extends QueryResultRow {
  fileId: string;
  status: FileObjectStatus;
  mimeType: string;
  sizeBytes: string;
  sha256: string;
  storageProvider: FileStorageProvider;
  objectKey: string;
  originalFilename: string;
  expiresAt: Date;
  uploadReceivedAt: Date | null;
  requestHash: string;
}

export type CreateIntentResult =
  | { type: 'CREATED' | 'REPLAY'; file: OwnedFileRecord }
  | { type: 'IDEMPOTENCY_CONFLICT' }
  | { type: 'TASK_STEP_NOT_FOUND' };

@Injectable()
export class FileRepository {
  constructor(private readonly database: DatabaseService) {}

  createIntent(input: {
    fileId: string;
    accountId: string;
    taskAssignmentId: string;
    stepKey: string;
    idempotencyKey: string;
    requestHash: string;
    storageProvider: FileStorageProvider;
    objectKey: string;
    originalFilename: string;
    mimeType: string;
    sizeBytes: number;
    sha256: string;
    expiresAt: Date;
  }): Promise<CreateIntentResult> {
    return this.database.withTideTransaction(async (client) => {
      await client.query(
        `SELECT pg_advisory_xact_lock(hashtextextended($1 || ':' || $2, 0))`,
        [input.accountId, input.idempotencyKey],
      );
      const existing = await this.findByIdempotencyKey(
        client,
        input.accountId,
        input.idempotencyKey,
      );
      if (existing) {
        return existing.requestHash === input.requestHash
          ? { type: 'REPLAY', file: this.toOwnedFile(existing) }
          : { type: 'IDEMPOTENCY_CONFLICT' };
      }

      const taskStep = await client.query(
        `
          SELECT 1
          FROM public.task_assignments task
          JOIN tide.teacher_bindings binding
            ON binding.teacher_id = task.teacher_id
          JOIN tide.task_execution_versions execution
            ON execution.shared_template_row_id = task.template_version_id
          JOIN tide.task_step_definitions step
            ON step.execution_version_id = execution.id
           AND step.step_key = $3
           AND step.step_type = 'UPLOAD'
          WHERE task.assignment_id = $1
            AND binding.account_id = $2
            AND binding.status = 'ACTIVE'
            AND task.status IN ('ASSIGNED', 'VIEWED', 'IN_PROGRESS', 'FAILED')
          LIMIT 1
        `,
        [input.taskAssignmentId, input.accountId, input.stepKey],
      );
      if (taskStep.rowCount === 0) {
        return { type: 'TASK_STEP_NOT_FOUND' };
      }

      await client.query(
        `
          INSERT INTO tide.file_objects (
            id, uploader_account_id, storage_provider, object_key,
            original_filename, mime_type, size_bytes, sha256
          ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        `,
        [
          input.fileId,
          input.accountId,
          input.storageProvider,
          input.objectKey,
          input.originalFilename,
          input.mimeType,
          input.sizeBytes,
          input.sha256,
        ],
      );
      const inserted = await client.query<UploadIntentRow>(
        `
          INSERT INTO tide.file_upload_intents (
            id, file_id, account_id, task_assignment_id, step_key,
            idempotency_key, request_hash, expires_at
          ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
          RETURNING
            file_id AS "fileId", 'PENDING'::text AS status,
            $9::text AS "mimeType", $10::bigint AS "sizeBytes",
            $11::text AS sha256, $12::text AS "objectKey",
            $13::text AS "originalFilename", expires_at AS "expiresAt",
            upload_received_at AS "uploadReceivedAt", request_hash AS "requestHash",
            $14::text AS "storageProvider"
        `,
        [
          randomUUID(),
          input.fileId,
          input.accountId,
          input.taskAssignmentId,
          input.stepKey,
          input.idempotencyKey,
          input.requestHash,
          input.expiresAt,
          input.mimeType,
          input.sizeBytes,
          input.sha256,
          input.objectKey,
          input.originalFilename,
          input.storageProvider,
        ],
      );
      return { type: 'CREATED', file: this.toOwnedFile(inserted.rows[0]) };
    });
  }

  async findOwnedFile(
    fileId: string,
    accountId: string,
  ): Promise<OwnedFileRecord | null> {
    const result = await this.database.queryTide<UploadIntentRow>(
      `${this.ownedFileSelect()} WHERE file.id = $1 AND intent.account_id = $2 LIMIT 1`,
      [fileId, accountId],
    );
    return result.rows[0] ? this.toOwnedFile(result.rows[0]) : null;
  }

  markUploadReceived(fileId: string, accountId: string): Promise<void> {
    return this.database.withTideTransaction(async (client) => {
      await client.query(
        `UPDATE tide.file_upload_intents SET upload_received_at = now() WHERE file_id = $1 AND account_id = $2`,
        [fileId, accountId],
      );
      await this.recordAccess(client, fileId, accountId, 'UPLOAD', 'SUCCESS');
    });
  }

  markReady(fileId: string, accountId: string): Promise<void> {
    return this.database.withTideTransaction(async (client) => {
      await client.query(
        `UPDATE tide.file_objects SET status = 'READY', ready_at = COALESCE(ready_at, now()) WHERE id = $1 AND uploader_account_id = $2 AND status = 'PENDING'`,
        [fileId, accountId],
      );
    });
  }

  markQuarantined(fileId: string, accountId: string): Promise<void> {
    return this.database.withTideTransaction(async (client) => {
      await client.query(
        `UPDATE tide.file_objects SET status = 'QUARANTINED' WHERE id = $1 AND uploader_account_id = $2 AND status = 'PENDING'`,
        [fileId, accountId],
      );
      await this.recordAccess(client, fileId, accountId, 'UPLOAD', 'FAILED');
    });
  }

  recordDownload(
    fileId: string,
    accountId: string,
    outcome: 'SUCCESS' | 'DENIED' | 'FAILED',
  ): Promise<void> {
    return this.database.withTideTransaction((client) =>
      this.recordAccess(client, fileId, accountId, 'DOWNLOAD', outcome),
    );
  }

  private async findByIdempotencyKey(
    client: PoolClient,
    accountId: string,
    idempotencyKey: string,
  ): Promise<UploadIntentRow | null> {
    const result = await client.query<UploadIntentRow>(
      `${this.ownedFileSelect()} WHERE intent.account_id = $1 AND intent.idempotency_key = $2 LIMIT 1`,
      [accountId, idempotencyKey],
    );
    return result.rows[0] ?? null;
  }

  private ownedFileSelect(): string {
    return `
      SELECT
        file.id AS "fileId", file.status, file.mime_type AS "mimeType",
        file.size_bytes AS "sizeBytes", file.sha256,
        file.storage_provider AS "storageProvider",
        file.object_key AS "objectKey",
        file.original_filename AS "originalFilename",
        intent.expires_at AS "expiresAt",
        intent.upload_received_at AS "uploadReceivedAt",
        intent.request_hash AS "requestHash"
      FROM tide.file_upload_intents intent
      JOIN tide.file_objects file ON file.id = intent.file_id
    `;
  }

  private async recordAccess(
    client: PoolClient,
    fileId: string,
    accountId: string,
    action: 'UPLOAD' | 'DOWNLOAD',
    outcome: 'SUCCESS' | 'DENIED' | 'FAILED',
  ): Promise<void> {
    await client.query(
      `INSERT INTO tide.file_access_events (id, file_id, account_id, action, outcome) VALUES ($1, $2, $3, $4, $5)`,
      [randomUUID(), fileId, accountId, action, outcome],
    );
  }

  private toOwnedFile(row: UploadIntentRow): OwnedFileRecord {
    return {
      fileId: row.fileId,
      status: row.status,
      mimeType: row.mimeType,
      sizeBytes: Number(row.sizeBytes),
      sha256: row.sha256,
      storageProvider: row.storageProvider,
      objectKey: row.objectKey,
      originalFilename: row.originalFilename,
      expiresAt: row.expiresAt,
      uploadReceivedAt: row.uploadReceivedAt,
    };
  }
}
