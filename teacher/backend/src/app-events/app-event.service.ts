import {
  BadRequestException,
  Injectable,
  NotFoundException,
  PayloadTooLargeException,
} from '@nestjs/common';
import type { AuthPrincipal } from '../auth/auth.models';
import type { CreateAppEventDto } from './dto/create-app-event.dto';
import type {
  AppEventBatchAccepted,
  SystemAppEventInput,
} from './app-event.models';
import {
  ALLOWED_APP_EVENT_PROPERTIES,
  ANONYMOUS_CLIENT_EVENTS,
  APP_EVENT_SCHEMA_VERSION,
  TASK_REQUIRED_EVENTS,
  type AppEventName,
  clientCanWriteEvent,
  isAppEventName,
  isSensitivePropertyName,
} from './app-event.dictionary';
import {
  AppEventOwnershipError,
  AppEventRepository,
} from './app-event.repository';

const MAX_PROPERTIES_BYTES = 8 * 1024;
const MAX_EVENT_AGE_MS = 7 * 24 * 60 * 60 * 1000;
const MAX_FUTURE_SKEW_MS = 5 * 60 * 1000;

@Injectable()
export class AppEventService {
  constructor(private readonly repository: AppEventRepository) {}

  async save(principal: AuthPrincipal, input: CreateAppEventDto) {
    this.validateClientEvent(input);
    try {
      return await this.repository.saveClient(
        principal.accountId,
        principal.sessionId,
        input,
      );
    } catch (error) {
      if (error instanceof AppEventOwnershipError) {
        throw new NotFoundException({
          code: 'RELATED_TASK_NOT_FOUND',
          message: '未找到关联任务或教师身份',
          retryable: false,
        });
      }
      throw error;
    }
  }

  async saveBatch(
    principal: AuthPrincipal,
    inputs: CreateAppEventDto[],
  ): Promise<AppEventBatchAccepted> {
    const failures: AppEventBatchAccepted['failures'] = [];
    const valid = inputs.flatMap((input, index) => {
      try {
        this.validateClientEvent(input);
        return [{ index, input }];
      } catch (error) {
        failures.push({ index, code: this.validationCode(error) });
        return [];
      }
    });
    if (valid.length === 0) return { acceptedEventIds: [], failures };

    const saved = await this.repository.saveClientBatch(
      principal.accountId,
      principal.sessionId,
      valid,
    );
    failures.push(
      ...saved.ownershipFailureIndexes.map((index) => ({
        index,
        code: 'RELATED_TASK_NOT_FOUND',
      })),
    );
    return {
      acceptedEventIds: saved.acceptedEventIds,
      failures: failures.sort((left, right) => left.index - right.index),
    };
  }

  saveAnonymous(input: CreateAppEventDto) {
    this.validateClientEvent(input);
    this.validateAnonymous(input);
    return this.repository.saveAnonymousClient(input);
  }

  async saveAnonymousBatch(
    inputs: CreateAppEventDto[],
  ): Promise<AppEventBatchAccepted> {
    const failures: AppEventBatchAccepted['failures'] = [];
    const valid = inputs.flatMap((input, index) => {
      try {
        this.validateClientEvent(input);
        this.validateAnonymous(input);
        return [{ index, input }];
      } catch (error) {
        failures.push({ index, code: this.validationCode(error) });
        return [];
      }
    });
    const acceptedEventIds =
      valid.length === 0
        ? []
        : await this.repository.saveAnonymousClientBatch(
            valid.map((item) => item.input),
          );
    return { acceptedEventIds, failures };
  }

  captureSystem(input: SystemAppEventInput): void {
    if (!isAppEventName(input.eventName)) return;
    if (!input.sessionId || input.sessionId.length > 128) return;
    try {
      this.validateProperties(input.properties ?? {});
    } catch {
      return;
    }
    void this.repository.saveSystem(input).catch(() => undefined);
  }

  private validateClientEvent(input: CreateAppEventDto): void {
    if (
      !input ||
      typeof input.eventName !== 'string' ||
      typeof input.eventId !== 'string' ||
      input.eventId.length < 8 ||
      input.eventId.length > 128 ||
      typeof input.sessionId !== 'string' ||
      input.sessionId.length < 8 ||
      input.sessionId.length > 128 ||
      input.eventSchemaVersion !== APP_EVENT_SCHEMA_VERSION ||
      !input.properties ||
      typeof input.properties !== 'object' ||
      Array.isArray(input.properties) ||
      typeof input.occurredAt !== 'string' ||
      (input.taskAssignmentId !== undefined &&
        (typeof input.taskAssignmentId !== 'string' ||
          input.taskAssignmentId.length < 1 ||
          input.taskAssignmentId.length > 255))
    ) {
      throw new BadRequestException({
        code: 'APP_EVENT_INVALID',
        message: '事件格式无效',
        retryable: false,
      });
    }
    if (
      !isAppEventName(input.eventName) ||
      !clientCanWriteEvent(input.eventName)
    ) {
      throw new BadRequestException({
        code: 'APP_EVENT_NOT_ALLOWED',
        message: '事件名称或版本不在当前埋点字典中',
        retryable: false,
      });
    }
    if (TASK_REQUIRED_EVENTS.has(input.eventName) && !input.taskAssignmentId) {
      throw new BadRequestException({
        code: 'APP_EVENT_TASK_REQUIRED',
        message: '该事件必须关联任务',
        retryable: false,
      });
    }
    const occurredAt = new Date(input.occurredAt).getTime();
    const now = Date.now();
    if (
      !Number.isFinite(occurredAt) ||
      occurredAt < now - MAX_EVENT_AGE_MS ||
      occurredAt > now + MAX_FUTURE_SKEW_MS
    ) {
      throw new BadRequestException({
        code: 'APP_EVENT_TIME_INVALID',
        message: '事件时间超出允许范围',
        retryable: false,
      });
    }
    this.validateProperties(input.properties);
  }

  private validateAnonymous(input: CreateAppEventDto): void {
    if (
      input.taskAssignmentId ||
      !ANONYMOUS_CLIENT_EVENTS.has(input.eventName as AppEventName)
    ) {
      throw new BadRequestException({
        code: 'APP_EVENT_AUTH_REQUIRED',
        message: '该事件必须登录后上报',
        retryable: false,
      });
    }
  }

  private validationCode(error: unknown): string {
    const exception = error as { getResponse?: () => unknown } | null;
    if (typeof exception?.getResponse === 'function') {
      const response = exception.getResponse();
      if (
        response &&
        typeof response === 'object' &&
        'code' in response &&
        typeof response.code === 'string'
      ) {
        return response.code;
      }
    }
    return 'APP_EVENT_INVALID';
  }

  private validateProperties(properties: Record<string, unknown>): void {
    const encoded = JSON.stringify(properties);
    if (Buffer.byteLength(encoded, 'utf8') > MAX_PROPERTIES_BYTES) {
      throw new PayloadTooLargeException({
        code: 'APP_EVENT_PROPERTIES_TOO_LARGE',
        message: '事件属性超过 8 KB',
        retryable: false,
      });
    }
    for (const [key, value] of Object.entries(properties)) {
      if (
        !ALLOWED_APP_EVENT_PROPERTIES.has(key) ||
        isSensitivePropertyName(key)
      ) {
        throw new BadRequestException({
          code: 'APP_EVENT_PROPERTY_NOT_ALLOWED',
          message: `事件属性不允许：${key}`,
          retryable: false,
        });
      }
      if (
        value !== null &&
        !['string', 'number', 'boolean'].includes(typeof value)
      ) {
        throw new BadRequestException({
          code: 'APP_EVENT_PROPERTY_INVALID',
          message: `事件属性必须为扁平基础类型：${key}`,
          retryable: false,
        });
      }
      if (typeof value === 'string' && value.length > 512) {
        throw new BadRequestException({
          code: 'APP_EVENT_PROPERTY_INVALID',
          message: `事件属性过长：${key}`,
          retryable: false,
        });
      }
    }
  }
}
