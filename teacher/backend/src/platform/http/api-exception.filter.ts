import {
  ArgumentsHost,
  Catch,
  HttpException,
  HttpStatus,
  type ExceptionFilter,
} from '@nestjs/common';
import type { Response } from 'express';
import { PinoLogger } from 'nestjs-pino';
import type { RequestWithId } from './request-context';

interface ApiErrorResponse {
  code: string;
  message: string;
  requestId: string;
  retryable: boolean;
  details?: Record<string, unknown>;
}

@Catch()
export class ApiExceptionFilter implements ExceptionFilter {
  constructor(private readonly logger: PinoLogger) {
    this.logger.setContext(ApiExceptionFilter.name);
  }

  catch(exception: unknown, host: ArgumentsHost): void {
    const http = host.switchToHttp();
    const request = http.getRequest<RequestWithId>();
    const response = http.getResponse<Response>();
    const status =
      exception instanceof HttpException
        ? exception.getStatus()
        : HttpStatus.INTERNAL_SERVER_ERROR;
    const payload = this.toApiError(exception, status, request.id);

    if (status >= 500) {
      this.logger.error(
        { err: exception, requestId: request.id, path: request.path },
        payload.message,
      );
    } else {
      this.logger.warn(
        { requestId: request.id, path: request.path, code: payload.code },
        payload.message,
      );
    }

    response.status(status).json(payload);
  }

  private toApiError(
    exception: unknown,
    status: number,
    requestId: string,
  ): ApiErrorResponse {
    if (!(exception instanceof HttpException)) {
      return {
        code: 'INTERNAL_ERROR',
        message: '服务暂时不可用，请稍后重试',
        requestId,
        retryable: true,
      };
    }

    const response = exception.getResponse();
    const responseObject =
      typeof response === 'object' && response !== null ? response : null;
    const responseRecord = responseObject as Record<string, unknown> | null;
    const message = this.resolveMessage(responseRecord?.message ?? response);
    const code =
      typeof responseRecord?.code === 'string'
        ? responseRecord.code
        : `HTTP_${status}`;
    const details =
      typeof responseRecord?.details === 'object' &&
      responseRecord.details !== null
        ? (responseRecord.details as Record<string, unknown>)
        : undefined;

    return {
      code,
      message,
      requestId,
      retryable:
        typeof responseRecord?.retryable === 'boolean'
          ? responseRecord.retryable
          : status >= 500,
      ...(details ? { details } : {}),
    };
  }

  private resolveMessage(message: unknown): string {
    if (Array.isArray(message)) {
      return message.filter((item) => typeof item === 'string').join('; ');
    }

    return typeof message === 'string' ? message : '请求处理失败';
  }
}
