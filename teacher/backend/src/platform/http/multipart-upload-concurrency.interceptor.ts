import {
  type CallHandler,
  type ExecutionContext,
  HttpException,
  HttpStatus,
  Injectable,
  type NestInterceptor,
} from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import type { Request, Response } from 'express';
import type { Observable } from 'rxjs';
import { finalize } from 'rxjs';
import type { AppEnvironment } from '../config/environment';

const RETRY_AFTER_SECONDS = 1;

@Injectable()
export class MultipartUploadConcurrencyInterceptor implements NestInterceptor {
  private readonly maxConcurrency: number;
  private activeUploads = 0;

  constructor(config: ConfigService<AppEnvironment, true>) {
    this.maxConcurrency = config.get('MULTIPART_UPLOAD_MAX_CONCURRENCY', {
      infer: true,
    });
  }

  intercept(context: ExecutionContext, next: CallHandler): Observable<unknown> {
    const http = context.switchToHttp();
    const request = http.getRequest<Request>();
    if (!this.isMultipart(request.headers['content-type'])) {
      return next.handle();
    }

    const response = http.getResponse<Response>();
    if (this.activeUploads >= this.maxConcurrency) {
      response.setHeader('Retry-After', String(RETRY_AFTER_SECONDS));
      throw new HttpException(
        {
          code: 'MULTIPART_UPLOAD_CAPACITY_EXHAUSTED',
          message: '上传请求较多，请稍后重试',
          retryable: true,
          details: {
            maxConcurrency: this.maxConcurrency,
            retryAfterSeconds: RETRY_AFTER_SECONDS,
          },
        },
        HttpStatus.TOO_MANY_REQUESTS,
      );
    }

    this.activeUploads += 1;
    let released = false;
    const release = () => {
      if (released) return;
      released = true;
      this.activeUploads -= 1;
    };

    try {
      return next.handle().pipe(finalize(release));
    } catch (error) {
      release();
      throw error;
    }
  }

  private isMultipart(contentType: string | string[] | undefined): boolean {
    const value = Array.isArray(contentType) ? contentType[0] : contentType;
    return /^multipart\/form-data(?:;|$)/i.test(value?.trim() ?? '');
  }
}
