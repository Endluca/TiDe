import {
  BadGatewayException,
  Injectable,
  ServiceUnavailableException,
} from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { request as httpRequest } from 'node:http';
import { request as httpsRequest } from 'node:https';
import { z } from 'zod';
import type { AppEnvironment } from '../../platform/config/environment';

const MAX_DETAIL_RESPONSE_BYTES = 2 * 1024 * 1024;

interface DetailHttpResponse {
  ok: boolean;
  status: number;
  json(): Promise<unknown>;
}

const scalarNumber = z.union([z.number(), z.string()]).nullable().optional();
const scalarId = z.union([z.number(), z.string()]).nullable().optional();

const detailTaskSchema = z
  .object({
    id: scalarId,
    title: z.string().nullable().optional(),
    type: z.string().nullable().optional(),
    isOptional: scalarNumber,
    percent: scalarNumber,
    time: scalarNumber,
    watchTime: scalarNumber,
    score: scalarNumber,
    test_times: scalarNumber,
  })
  .passthrough();

const detailSchema = z
  .object({
    id: scalarId,
    title: z.string().nullable().optional(),
    percent: scalarNumber,
    task_list: z
      .union([
        z.record(z.string(), detailTaskSchema),
        z.array(detailTaskSchema),
      ])
      .nullable()
      .optional(),
  })
  .passthrough();

export type KuozhiCourseDetail = z.infer<typeof detailSchema>;
export type KuozhiCourseDetailTask = z.infer<typeof detailTaskSchema>;

@Injectable()
export class KuozhiDetailClient {
  constructor(private readonly config: ConfigService<AppEnvironment, true>) {}

  private requestWithBackendHostOverride(
    url: URL,
    hostIp: string,
    timeoutMs: number,
  ): Promise<DetailHttpResponse> {
    const connectUrl = new URL(url);
    connectUrl.hostname = hostIp;
    const request = url.protocol === 'https:' ? httpsRequest : httpRequest;

    return new Promise((resolve, reject) => {
      const outgoing = request(
        connectUrl,
        {
          headers: {
            Accept: 'application/vnd.edusoho.v2+json',
            Host: url.host,
          },
          servername: url.protocol === 'https:' ? url.hostname : undefined,
          signal: AbortSignal.timeout(timeoutMs),
        },
        (incoming) => {
          const chunks: Buffer[] = [];
          let size = 0;
          incoming.on('data', (chunk: Buffer | string) => {
            const buffer = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
            size += buffer.length;
            if (size > MAX_DETAIL_RESPONSE_BYTES) {
              outgoing.destroy(new Error('KUOZHI_DETAIL_RESPONSE_TOO_LARGE'));
              return;
            }
            chunks.push(buffer);
          });
          incoming.once('end', () => {
            const status = incoming.statusCode ?? 0;
            const body = Buffer.concat(chunks).toString('utf8');
            resolve({
              ok: status >= 200 && status < 300,
              status,
              json: () => Promise.resolve(JSON.parse(body) as unknown),
            });
          });
        },
      );
      outgoing.once('error', reject);
      outgoing.end();
    });
  }

  private requestDetail(url: URL): Promise<DetailHttpResponse> {
    const timeoutMs = this.config.get('KUOZHI_DETAIL_TIMEOUT_MS', {
      infer: true,
    });
    const hostIp = this.config.get('KUOZHI_DETAIL_HOST_IP', { infer: true });
    if (hostIp) {
      return this.requestWithBackendHostOverride(url, hostIp, timeoutMs);
    }
    return fetch(url, {
      headers: { Accept: 'application/vnd.edusoho.v2+json' },
      signal: AbortSignal.timeout(timeoutMs),
    });
  }

  async getCourseDetail(
    teacherId: string,
    courseId: string,
  ): Promise<KuozhiCourseDetail> {
    const url = new URL(this.config.get('KUOZHI_DETAIL_URL', { infer: true }));
    url.searchParams.set('course_id', courseId);
    url.searchParams.set('t_id', teacherId);

    const attempts =
      this.config.get('KUOZHI_DETAIL_RETRY_COUNT', { infer: true }) + 1;
    let lastError: unknown;
    for (let attempt = 0; attempt < attempts; attempt += 1) {
      try {
        const response = await this.requestDetail(url);
        if (!response.ok) {
          if (response.status >= 500 && attempt + 1 < attempts) continue;
          throw new BadGatewayException({
            code: 'KUOZHI_DETAIL_HTTP_ERROR',
            message: '课程进度服务返回异常，请稍后重试',
            retryable: response.status >= 500,
          });
        }
        let payload: unknown;
        try {
          payload = await response.json();
        } catch {
          throw new BadGatewayException({
            code: 'KUOZHI_DETAIL_CONTRACT_INVALID',
            message: '课程进度数据格式异常，请稍后重试',
            retryable: false,
          });
        }
        const parsed = detailSchema.safeParse(payload);
        if (!parsed.success) {
          throw new BadGatewayException({
            code: 'KUOZHI_DETAIL_CONTRACT_INVALID',
            message: '课程进度数据格式异常，请稍后重试',
            retryable: false,
          });
        }
        return parsed.data;
      } catch (error) {
        if (error instanceof BadGatewayException) throw error;
        lastError = error;
        if (attempt + 1 >= attempts) break;
      }
    }

    throw new ServiceUnavailableException({
      code: 'KUOZHI_DETAIL_UNAVAILABLE',
      message: '课程进度暂时无法同步，请稍后重试',
      retryable: true,
      details: {
        cause:
          lastError instanceof Error && lastError.name === 'TimeoutError'
            ? 'TIMEOUT'
            : 'NETWORK_ERROR',
      },
    });
  }
}
