import {
  BadRequestException,
  ConflictException,
  GoneException,
  Injectable,
  NotFoundException,
  PayloadTooLargeException,
  UnsupportedMediaTypeException,
} from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { createHash, randomUUID } from 'node:crypto';
import { basename, win32 } from 'node:path';
import { Transform, type Readable } from 'node:stream';
import type { AuthPrincipal } from '../auth/auth.models';
import type { AppEnvironment } from '../platform/config/environment';
import { CompleteUploadDto } from './dto/complete-upload.dto';
import { CreateUploadIntentDto } from './dto/create-upload-intent.dto';
import type {
  DownloadedFile,
  FileObjectResponse,
  OwnedFileRecord,
  UploadIntentResponse,
} from './file.models';
import { FileRepository } from './file.repository';
import { FileStorageAdapter } from './file-storage.adapter';

@Injectable()
export class FileService {
  private readonly publicApiUrl: string;
  private readonly maxBytes: number;
  private readonly uploadTtlMinutes: number;
  private readonly allowedMimeTypes: Set<string>;

  constructor(
    private readonly repository: FileRepository,
    private readonly storage: FileStorageAdapter,
    config: ConfigService<AppEnvironment, true>,
  ) {
    this.publicApiUrl = config
      .get('PUBLIC_API_URL', { infer: true })
      .replace(/\/$/, '');
    this.maxBytes = config.get('FILE_UPLOAD_MAX_BYTES', { infer: true });
    this.uploadTtlMinutes = config.get('FILE_UPLOAD_TTL_MINUTES', {
      infer: true,
    });
    this.allowedMimeTypes = new Set(
      config
        .get('FILE_ALLOWED_MIME_TYPES', { infer: true })
        .split(',')
        .map((value) => value.trim().toLowerCase())
        .filter(Boolean),
    );
  }

  async createUploadIntent(
    principal: AuthPrincipal,
    idempotencyKey: string | undefined,
    input: CreateUploadIntentDto,
  ): Promise<UploadIntentResponse> {
    this.assertIdempotencyKey(idempotencyKey);
    this.assertFilePolicy(input.mimeType, input.sizeBytes);

    const filename = this.safeFilename(input.filename);
    const fileId = randomUUID();
    const expiresAt = new Date(Date.now() + this.uploadTtlMinutes * 60 * 1_000);
    const requestHash = createHash('sha256')
      .update(
        JSON.stringify({
          taskInstanceId: input.taskInstanceId,
          stepKey: input.stepKey,
          filename,
          mimeType: input.mimeType.toLowerCase(),
          sizeBytes: input.sizeBytes,
          sha256: input.sha256,
        }),
      )
      .digest('hex');
    const result = await this.repository.createIntent({
      fileId,
      accountId: principal.accountId,
      taskAssignmentId: input.taskInstanceId,
      stepKey: input.stepKey,
      idempotencyKey: idempotencyKey!,
      requestHash,
      storageProvider: this.storage.activeProvider,
      objectKey: `uploads/${principal.accountId}/${fileId}`,
      originalFilename: filename,
      mimeType: input.mimeType.toLowerCase(),
      sizeBytes: input.sizeBytes,
      sha256: input.sha256,
      expiresAt,
    });

    if (result.type === 'IDEMPOTENCY_CONFLICT') {
      throw new ConflictException({
        code: 'IDEMPOTENCY_KEY_REUSED',
        message: '该幂等键已用于其他上传请求',
        retryable: false,
      });
    }
    if (result.type === 'TASK_STEP_NOT_FOUND') {
      throw new NotFoundException({
        code: 'UPLOAD_STEP_NOT_FOUND',
        message: '未找到可上传文件的任务步骤',
        retryable: false,
      });
    }

    return this.toIntentResponse(result.file);
  }

  async uploadStream(
    principal: AuthPrincipal,
    fileId: string,
    content: Readable,
    mimeType: string | undefined,
    contentLength: string | undefined,
  ): Promise<FileObjectResponse> {
    const file = await this.findOwned(fileId, principal.accountId);
    this.assertPendingAndUnexpired(file);

    const normalizedMimeType = mimeType?.split(';', 1)[0]?.trim().toLowerCase();
    const declaredSize =
      contentLength && /^\d+$/.test(contentLength)
        ? Number(contentLength)
        : null;
    if (
      normalizedMimeType !== file.mimeType ||
      (declaredSize !== null && declaredSize !== file.sizeBytes)
    ) {
      await this.quarantineAndDelete(file, principal.accountId);
      throw this.metadataMismatch();
    }

    let receivedBytes = 0;
    let streamMismatch = false;
    const digest = createHash('sha256');
    const integrityStream = new Transform({
      transform: (chunk: Buffer, _encoding, callback) => {
        receivedBytes += chunk.length;
        if (receivedBytes > file.sizeBytes || receivedBytes > this.maxBytes) {
          streamMismatch = true;
          callback(new Error('FILE_METADATA_MISMATCH'));
          return;
        }
        digest.update(chunk);
        callback(null, chunk);
      },
    });

    content.pipe(integrityStream);
    try {
      await this.storage.writeStream(
        file.objectKey,
        integrityStream,
        file.storageProvider,
      );
    } catch (error) {
      await this.storage
        .delete(file.objectKey, file.storageProvider)
        .catch(() => undefined);
      if (streamMismatch) {
        await this.repository.markQuarantined(fileId, principal.accountId);
        throw this.metadataMismatch();
      }
      throw error;
    }

    if (
      receivedBytes !== file.sizeBytes ||
      digest.digest('hex') !== file.sha256
    ) {
      await this.quarantineAndDelete(file, principal.accountId);
      throw this.metadataMismatch();
    }

    await this.repository.markUploadReceived(fileId, principal.accountId);
    return this.toFileResponse(file);
  }

  async completeUpload(
    principal: AuthPrincipal,
    fileId: string,
    idempotencyKey: string | undefined,
    input: CompleteUploadDto,
  ): Promise<FileObjectResponse> {
    this.assertIdempotencyKey(idempotencyKey);
    const file = await this.findOwned(fileId, principal.accountId);

    if (input.sha256 !== file.sha256) {
      throw new ConflictException({
        code: 'FILE_SHA256_MISMATCH',
        message: '文件摘要与上传意图不一致',
        retryable: false,
      });
    }
    if (file.status === 'READY') {
      return this.toFileResponse(file);
    }
    this.assertPendingAndUnexpired(file);
    if (!file.uploadReceivedAt && file.storageProvider !== 'OSS') {
      throw new ConflictException({
        code: 'FILE_CONTENT_NOT_UPLOADED',
        message: '文件内容尚未上传',
        retryable: true,
      });
    }

    const valid =
      file.storageProvider === 'OSS'
        ? await this.verifyDirectUpload(file)
        : await this.verifyStoredContent(file);
    if (!valid) {
      await this.repository.markQuarantined(fileId, principal.accountId);
      await this.storage
        .delete(file.objectKey, file.storageProvider)
        .catch(() => undefined);
      throw new ConflictException({
        code: 'FILE_INTEGRITY_CHECK_FAILED',
        message: '文件完整性校验失败',
        retryable: false,
      });
    }

    if (!file.uploadReceivedAt) {
      await this.repository.markUploadReceived(fileId, principal.accountId);
    }
    await this.repository.markReady(fileId, principal.accountId);
    return { ...this.toFileResponse(file), status: 'READY' };
  }

  async download(
    principal: AuthPrincipal,
    fileId: string,
  ): Promise<DownloadedFile> {
    const file = await this.findOwned(fileId, principal.accountId);
    if (file.status !== 'READY') {
      await this.repository.recordDownload(
        fileId,
        principal.accountId,
        'DENIED',
      );
      throw new ConflictException({
        code: 'FILE_NOT_READY',
        message: '文件尚未完成校验',
        retryable: file.status === 'PENDING',
      });
    }

    try {
      const content = await this.storage.read(
        file.objectKey,
        file.storageProvider,
      );
      await this.repository.recordDownload(
        fileId,
        principal.accountId,
        'SUCCESS',
      );
      return {
        content,
        mimeType: file.mimeType,
        originalFilename: file.originalFilename,
      };
    } catch {
      await this.repository.recordDownload(
        fileId,
        principal.accountId,
        'FAILED',
      );
      throw new ConflictException({
        code: 'FILE_CONTENT_UNAVAILABLE',
        message: '文件内容暂时不可用',
        retryable: true,
      });
    }
  }

  private async findOwned(
    fileId: string,
    accountId: string,
  ): Promise<OwnedFileRecord> {
    const file = await this.repository.findOwnedFile(fileId, accountId);
    if (!file) {
      throw new NotFoundException({
        code: 'FILE_NOT_FOUND',
        message: '未找到该文件',
        retryable: false,
      });
    }
    return file;
  }

  private assertPendingAndUnexpired(file: OwnedFileRecord): void {
    if (file.status !== 'PENDING') {
      throw new ConflictException({
        code: 'FILE_UPLOAD_NOT_PENDING',
        message: '该文件已不处于待上传状态',
        retryable: false,
      });
    }
    if (file.expiresAt.getTime() <= Date.now()) {
      throw new GoneException({
        code: 'UPLOAD_INTENT_EXPIRED',
        message: '上传意图已过期，请重新创建',
        retryable: true,
      });
    }
  }

  private assertFilePolicy(mimeType: string, sizeBytes: number): void {
    if (sizeBytes > this.maxBytes) {
      throw new PayloadTooLargeException({
        code: 'FILE_TOO_LARGE',
        message: `文件大小不能超过 ${this.maxBytes} 字节`,
        retryable: false,
      });
    }
    if (!this.allowedMimeTypes.has(mimeType.toLowerCase())) {
      throw new UnsupportedMediaTypeException({
        code: 'FILE_TYPE_NOT_ALLOWED',
        message: '不支持该文件类型',
        retryable: false,
      });
    }
  }

  private assertIdempotencyKey(value: string | undefined): void {
    if (!value || value.length < 8 || value.length > 128) {
      throw new BadRequestException({
        code: 'INVALID_IDEMPOTENCY_KEY',
        message: 'Idempotency-Key 长度必须为 8 到 128 个字符',
        retryable: false,
      });
    }
  }

  private safeFilename(value: string): string {
    const name = [...basename(win32.basename(value))]
      .filter((character) => {
        const code = character.charCodeAt(0);
        return code >= 32 && code !== 127;
      })
      .join('')
      .trim();
    if (!name) {
      throw new BadRequestException({
        code: 'INVALID_FILENAME',
        message: '文件名无效',
        retryable: false,
      });
    }
    return name.slice(0, 255);
  }

  private async toIntentResponse(
    file: OwnedFileRecord,
  ): Promise<UploadIntentResponse> {
    const direct = await this.storage.createDirectUploadForm(
      file.objectKey,
      {
        mimeType: file.mimeType,
        sizeBytes: file.sizeBytes,
        sha256: file.sha256,
        expiresAt: file.expiresAt,
      },
      file.storageProvider,
    );
    return direct
      ? {
          fileId: file.fileId,
          uploadMethod: 'OSS_POST_FORM',
          uploadUrl: direct.uploadUrl,
          expiresAt: file.expiresAt.toISOString(),
          requiredHeaders: {},
          requiredFields: direct.fields,
        }
      : {
          fileId: file.fileId,
          uploadMethod: 'LOCAL_STREAM',
          uploadUrl: `${this.publicApiUrl}/api/v1/files/${file.fileId}/content`,
          expiresAt: file.expiresAt.toISOString(),
          requiredHeaders: { 'Content-Type': file.mimeType },
          requiredFields: {},
        };
  }

  private async quarantineAndDelete(
    file: OwnedFileRecord,
    accountId: string,
  ): Promise<void> {
    await this.repository.markQuarantined(file.fileId, accountId);
    await this.storage
      .delete(file.objectKey, file.storageProvider)
      .catch(() => undefined);
  }

  private metadataMismatch(): ConflictException {
    return new ConflictException({
      code: 'FILE_METADATA_MISMATCH',
      message: '文件内容与上传意图中的大小、类型或摘要不一致',
      retryable: false,
    });
  }

  private async verifyDirectUpload(file: OwnedFileRecord): Promise<boolean> {
    try {
      const metadata = await this.storage.stat(
        file.objectKey,
        file.storageProvider,
      );
      return Boolean(
        metadata &&
        metadata.sizeBytes === file.sizeBytes &&
        metadata.mimeType === file.mimeType &&
        metadata.sha256 === file.sha256,
      );
    } catch {
      throw new ConflictException({
        code: 'FILE_CONTENT_UNAVAILABLE',
        message: '暂未找到已上传的文件内容',
        retryable: true,
      });
    }
  }

  private async verifyStoredContent(file: OwnedFileRecord): Promise<boolean> {
    let content: Buffer;
    try {
      content = await this.storage.read(file.objectKey, file.storageProvider);
    } catch {
      throw new ConflictException({
        code: 'FILE_CONTENT_UNAVAILABLE',
        message: '暂未找到已上传的文件内容',
        retryable: true,
      });
    }
    return (
      content.length === file.sizeBytes &&
      createHash('sha256').update(content).digest('hex') === file.sha256
    );
  }

  private toFileResponse(file: OwnedFileRecord): FileObjectResponse {
    return {
      fileId: file.fileId,
      status: file.status,
      mimeType: file.mimeType,
      sizeBytes: file.sizeBytes,
      sha256: file.sha256,
    };
  }
}
