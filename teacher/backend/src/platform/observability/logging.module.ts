import { Global, Module } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { randomUUID } from 'node:crypto';
import type { IncomingMessage, ServerResponse } from 'node:http';
import { LoggerModule } from 'nestjs-pino';
import type { AppEnvironment } from '../config/environment';
import { DependencyHealthRegistry } from './dependency-health.registry';

const REQUEST_ID_PATTERN = /^[A-Za-z0-9._:-]{8,128}$/;

export function sanitizeRequestUrl(value: unknown): string {
  return typeof value === 'string' ? value.split('?')[0] : '';
}

export function requestLogLevel(
  request: IncomingMessage,
  response: ServerResponse,
  error?: Error,
): 'error' | 'warn' | 'info' | 'silent' {
  const statusCode = response.statusCode || 0;
  if (error || statusCode >= 500) return 'error';
  if (statusCode >= 400) return 'warn';
  const path = sanitizeRequestUrl(request.url);
  if (path.startsWith('/api/v1/app-events')) {
    return Math.random() < 0.02 ? 'info' : 'silent';
  }
  if (path.includes('/video-heartbeat')) {
    return Math.random() < 0.05 ? 'info' : 'silent';
  }
  return 'info';
}

function resolveRequestId(
  request: IncomingMessage,
  response: ServerResponse,
): string {
  const incoming = request.headers['x-request-id'];
  const candidate = Array.isArray(incoming) ? incoming[0] : incoming;
  const requestId =
    candidate && REQUEST_ID_PATTERN.test(candidate) ? candidate : randomUUID();

  response.setHeader('x-request-id', requestId);
  return requestId;
}

@Global()
@Module({
  imports: [
    LoggerModule.forRootAsync({
      inject: [ConfigService],
      useFactory: (config: ConfigService<AppEnvironment, true>) => ({
        pinoHttp: {
          level:
            config.get('NODE_ENV', { infer: true }) === 'test'
              ? 'silent'
              : config.get('LOG_LEVEL', { infer: true }),
          genReqId: resolveRequestId,
          customLogLevel: requestLogLevel,
          serializers: {
            req: (request: {
              id?: unknown;
              method?: unknown;
              url?: unknown;
              remoteAddress?: unknown;
              remotePort?: unknown;
            }) => ({
              id: request.id,
              method: request.method,
              url: sanitizeRequestUrl(request.url),
              remoteAddress: request.remoteAddress,
              remotePort: request.remotePort,
            }),
          },
          redact: {
            paths: [
              'req.headers.authorization',
              'req.headers.cookie',
              'res.headers.set-cookie',
            ],
            censor: '[REDACTED]',
          },
          autoLogging: {
            ignore: (request) => request.url === '/health',
          },
        },
      }),
    }),
  ],
  providers: [DependencyHealthRegistry],
  exports: [LoggerModule, DependencyHealthRegistry],
})
export class LoggingModule {}
