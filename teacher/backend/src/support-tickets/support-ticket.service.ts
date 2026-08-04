import {
  BadRequestException,
  ConflictException,
  Injectable,
  Logger,
  NotFoundException,
  PayloadTooLargeException,
  UnsupportedMediaTypeException,
} from '@nestjs/common';
import { createHash, randomUUID } from 'node:crypto';
import { extname } from 'node:path';
import sharp from 'sharp';
import type { AuthPrincipal } from '../auth/auth.models';
import { FileStorageAdapter } from '../files/file-storage.adapter';
import { JobLeaseLostError } from '../platform/database/job-lease.service';
import type {
  CreateSupportTicketDto,
  ReplySupportTicketDto,
} from './dto/create-support-ticket.dto';
import type {
  SupportTicketMessage,
  SupportTicketResponse,
  SupportTicketRow,
} from './support-ticket.models';
import {
  SupportTicketRepository,
  type StoredSupportImage,
} from './support-ticket.repository';

const maxImageBytes = 8 * 1024 * 1024;
const allowedMimeTypes = new Set(['image/jpeg', 'image/png', 'image/webp']);
const contextKeys = new Set([
  'taskAssignmentId',
  'taskCode',
  'taskName',
  'lessonId',
  'lessonDate',
  'lessonTime',
  'myTideLastUpdated',
  'errorMessage',
  'errorCode',
  'requestId',
  'entrySource',
  'pagePath',
  'language',
  'clientVersion',
  'browser',
  'operatingSystem',
  'deviceType',
  'relatedObjectUnavailable',
]);
const contextArrayKeys = new Set([
  'taskAssignmentIds',
  'taskCodes',
  'taskNames',
  'lessonIds',
  'lessonDates',
  'lessonTimes',
]);

@Injectable()
export class SupportTicketService {
  private readonly logger = new Logger(SupportTicketService.name);

  constructor(
    private readonly repository: SupportTicketRepository,
    private readonly storage: FileStorageAdapter,
  ) {}

  async create(
    principal: AuthPrincipal,
    input: CreateSupportTicketDto,
    files: Express.Multer.File[] | undefined,
    userAgent: string | undefined,
  ): Promise<SupportTicketResponse> {
    const ticketId = randomUUID();
    const messageId = randomUUID();
    const stored = await this.storeImages(ticketId, messageId, files);
    try {
      const row = await this.repository.create({
        accountId: principal.accountId,
        ticketId,
        secondaryCategory: input.secondaryCategory,
        problemLocation: input.problemLocation,
        context: this.context(input.context, userAgent),
        message: this.message(messageId, input.description, stored),
        images: stored,
      });
      if (!row) throw this.teacherNotFound();
      return this.response(row);
    } catch (error) {
      await this.deleteStored(stored);
      throw this.databaseError(error);
    }
  }

  async list(principal: AuthPrincipal): Promise<{
    items: SupportTicketResponse[];
    unreadCount: number;
  }> {
    const items = (await this.repository.list(principal.accountId)).map((row) =>
      this.response(row),
    );
    return {
      items,
      unreadCount: items.filter((item) => item.unread).length,
    };
  }

  async get(
    principal: AuthPrincipal,
    ticketId: string,
  ): Promise<SupportTicketResponse> {
    const row = await this.repository.findOwned(principal.accountId, ticketId);
    if (!row) throw this.notFound();
    return this.response(row);
  }

  async markRead(
    principal: AuthPrincipal,
    ticketId: string,
  ): Promise<SupportTicketResponse> {
    const row = await this.repository.markRead(principal.accountId, ticketId);
    if (!row) throw this.notFound();
    return this.response(row);
  }

  async reply(
    principal: AuthPrincipal,
    ticketId: string,
    input: ReplySupportTicketDto,
    files: Express.Multer.File[] | undefined,
  ): Promise<SupportTicketResponse> {
    const messageId = randomUUID();
    const stored = await this.storeImages(ticketId, messageId, files);
    try {
      const row = await this.repository.appendTeacherMessage({
        accountId: principal.accountId,
        ticketId,
        expectedRowVersion: input.rowVersion,
        message: this.message(messageId, input.description, stored),
        images: stored,
      });
      if (!row) throw this.notFoundOrConflict();
      return this.response(row);
    } catch (error) {
      await this.deleteStored(stored);
      throw this.databaseError(error);
    }
  }

  async resolve(
    principal: AuthPrincipal,
    ticketId: string,
    rowVersion: number,
  ): Promise<SupportTicketResponse> {
    const row = await this.repository.resolve(
      principal.accountId,
      ticketId,
      rowVersion,
    );
    if (!row) throw this.notFoundOrConflict();
    await this.cleanupTicket(row.ticketId, row.messages);
    const refreshed = await this.repository.findOwned(
      principal.accountId,
      ticketId,
    );
    return this.response(refreshed ?? row);
  }

  async image(
    principal: AuthPrincipal,
    ticketId: string,
    fileId: string,
  ): Promise<{
    content: Buffer;
    filename: string;
    mimeType: string;
  }> {
    const image = await this.repository.findOwnedImage(
      principal.accountId,
      ticketId,
      fileId,
    );
    if (!image) throw this.notFound();
    return {
      content: await this.storage.read(
        image.objectKey,
        image.storageProvider ?? this.storage.activeProvider,
      ),
      filename: image.filename,
      mimeType: image.mimeType,
    };
  }

  async closeExpiredAndCleanup(
    limit = 20,
    assertLeaseActive: () => void = () => undefined,
  ): Promise<void> {
    assertLeaseActive();
    await this.repository.closeExpired(limit);
    assertLeaseActive();
    const candidates = await this.repository.cleanupCandidates(limit);
    assertLeaseActive();
    for (const ticket of candidates) {
      assertLeaseActive();
      await this.cleanupTicket(
        ticket.ticketId,
        ticket.messages,
        assertLeaseActive,
      );
    }
  }

  private async cleanupTicket(
    ticketId: string,
    knownMessages?: SupportTicketMessage[],
    assertLeaseActive: () => void = () => undefined,
  ): Promise<void> {
    assertLeaseActive();
    const candidate =
      knownMessages ??
      (await this.repository.cleanupCandidates(100)).find(
        (item) => item.ticketId === ticketId,
      )?.messages;
    if (!candidate) return;
    const images = candidate.flatMap((message) => message.images ?? []);
    try {
      for (const image of images) {
        assertLeaseActive();
        if (image.deleted_at) continue;
        this.assertTicketObjectKey(ticketId, image.object_key);
        await this.storage.delete(
          image.object_key,
          image.storage_provider ?? this.storage.activeProvider,
        );
      }
      assertLeaseActive();
      await this.repository.markImagesDeleted(
        ticketId,
        images.map((image) => image.object_key),
      );
    } catch (error) {
      if (error instanceof JobLeaseLostError) throw error;
      await this.repository.markCleanupFailed(ticketId);
      this.logger.error({
        event: 'support_ticket_image_cleanup_failed',
        ticketId,
        error: error instanceof Error ? error.message : 'Unknown error',
      });
    }
  }

  private async storeImages(
    ticketId: string,
    messageId: string,
    files: Express.Multer.File[] | undefined,
  ): Promise<StoredSupportImage[]> {
    const uploads = files ?? [];
    if (uploads.length > 3) {
      throw new BadRequestException({
        code: 'SUPPORT_TICKET_TOO_MANY_IMAGES',
        message: '每次最多上传 3 张截图',
        retryable: false,
      });
    }
    const stored: StoredSupportImage[] = [];
    try {
      for (const file of uploads) {
        await this.assertImage(file);
        const fileId = randomUUID();
        const mimeType = file.mimetype.toLowerCase();
        const objectKey =
          `support-tickets/${ticketId}/${messageId}/${fileId}` +
          this.extension(mimeType);
        await this.storage.write(objectKey, file.buffer);
        stored.push({
          file_id: fileId,
          object_key: objectKey,
          filename: this.filename(file.originalname),
          mime_type: mimeType,
          size: file.size,
          sha256: createHash('sha256').update(file.buffer).digest('hex'),
          storage_provider: this.storage.activeProvider,
        });
      }
      return stored;
    } catch (error) {
      await this.deleteStored(stored);
      throw error;
    }
  }

  private async assertImage(file: Express.Multer.File): Promise<void> {
    if (file.size > maxImageBytes) {
      throw new PayloadTooLargeException({
        code: 'SUPPORT_TICKET_IMAGE_TOO_LARGE',
        message: '单张截图不能超过 8 MB',
        retryable: false,
      });
    }
    if (!allowedMimeTypes.has(file.mimetype.toLowerCase())) {
      throw new UnsupportedMediaTypeException({
        code: 'SUPPORT_TICKET_IMAGE_TYPE_UNSUPPORTED',
        message: '仅支持 JPG、PNG 或 WebP 截图',
        retryable: false,
      });
    }
    try {
      await sharp(file.buffer).metadata();
    } catch {
      throw new BadRequestException({
        code: 'SUPPORT_TICKET_IMAGE_INVALID',
        message: '截图文件无法读取，请重新选择',
        retryable: false,
      });
    }
  }

  private async deleteStored(images: StoredSupportImage[]): Promise<void> {
    await Promise.all(
      images.map((image) =>
        this.storage
          .delete(image.object_key, image.storage_provider)
          .catch(() => undefined),
      ),
    );
  }

  private message(
    messageId: string,
    description: string,
    images: StoredSupportImage[],
  ): Omit<SupportTicketMessage, 'created_at'> {
    return {
      message_id: messageId,
      sender: 'TEACHER',
      content: description.trim(),
      images,
    };
  }

  private context(
    input: Record<string, unknown> | undefined,
    userAgent: string | undefined,
  ): Record<string, unknown> {
    const context: Record<string, unknown> = {};
    for (const [key, value] of Object.entries(input ?? {})) {
      if (contextArrayKeys.has(key) && Array.isArray(value)) {
        context[key] = value
          .filter((item): item is string => typeof item === 'string')
          .slice(0, 50)
          .map((item) => item.slice(0, 2_000));
        continue;
      }
      if (
        contextKeys.has(key) &&
        (typeof value === 'string' ||
          typeof value === 'number' ||
          typeof value === 'boolean' ||
          value === null)
      ) {
        context[key] =
          typeof value === 'string' ? value.slice(0, 2_000) : value;
      }
    }
    if (userAgent) context.userAgent = userAgent.slice(0, 1_000);
    return context;
  }

  private response(row: SupportTicketRow): SupportTicketResponse {
    const latestOperatorMessage = [...row.messages]
      .reverse()
      .find((message) => message.sender === 'OPERATOR');
    const unread =
      latestOperatorMessage !== undefined &&
      latestOperatorMessage.message_id !== row.lastReadOperatorMessageId;
    return {
      ticketId: row.ticketId,
      primaryCategory: row.primaryCategory,
      secondaryCategory: row.secondaryCategory,
      problemLocation: row.problemLocation,
      problemContext: row.problemContext,
      messages: row.messages.map((message) => ({
        messageId: message.message_id,
        sender: message.sender,
        content: message.content,
        createdAt: message.created_at,
        images: (message.images ?? []).map((image) => {
          const fileId = image.file_id ?? '';
          const deleted = Boolean(image.deleted_at);
          return {
            fileId,
            filename: image.filename,
            mimeType: image.mime_type,
            size: image.size,
            deleted,
            contentUrl:
              !deleted && fileId
                ? `/api/v1/support-tickets/${row.ticketId}/images/${fileId}`
                : null,
          };
        }),
      })),
      status: row.status,
      unread,
      lastOperatorReplyAt: row.lastOperatorReplyAt?.toISOString() ?? null,
      teacherReplyDeadlineAt: row.teacherReplyDeadlineAt?.toISOString() ?? null,
      closeReason: row.closeReason,
      closedAt: row.closedAt?.toISOString() ?? null,
      rowVersion: Number(row.rowVersion),
      createdAt: row.createdAt.toISOString(),
      updatedAt: row.updatedAt.toISOString(),
    };
  }

  private extension(mimeType: string): string {
    if (mimeType === 'image/jpeg') return '.jpg';
    if (mimeType === 'image/png') return '.png';
    return '.webp';
  }

  private filename(value: string): string {
    return (
      value
        .replace(/[/\\\0]/g, '_')
        .slice(0, 160)
        .trim() || `screenshot${extname(value).slice(0, 10)}`
    );
  }

  private assertTicketObjectKey(ticketId: string, objectKey: string): void {
    if (!objectKey.startsWith(`support-tickets/${ticketId}/`)) {
      throw new Error('Support ticket image escaped its isolated prefix');
    }
  }

  private databaseError(error: unknown): unknown {
    if (
      error instanceof BadRequestException ||
      error instanceof NotFoundException ||
      error instanceof ConflictException
    ) {
      return error;
    }
    const code = (error as { code?: string }).code;
    if (code === '40001') return this.notFoundOrConflict();
    if (code === '55000') {
      return new ConflictException({
        code: 'SUPPORT_TICKET_CLOSED',
        message: '该工单已关闭，不能继续提交',
        retryable: false,
      });
    }
    return error;
  }

  private teacherNotFound(): NotFoundException {
    return new NotFoundException({
      code: 'TEACHER_BINDING_NOT_FOUND',
      message: '当前账号未关联教师信息',
      retryable: false,
    });
  }

  private notFound(): NotFoundException {
    return new NotFoundException({
      code: 'SUPPORT_TICKET_NOT_FOUND',
      message: '未找到该工单',
      retryable: false,
    });
  }

  private notFoundOrConflict(): ConflictException {
    return new ConflictException({
      code: 'SUPPORT_TICKET_CHANGED',
      message: '工单已有新回复，请刷新后重试',
      retryable: true,
    });
  }
}
