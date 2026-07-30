import type { ConfigService } from '@nestjs/config';
import type { AppEnvironment } from '../../platform/config/environment';
import { AiGatewayError } from './ai-gateway.models';
import { CompanyAiGatewayClient } from './company-ai-gateway.client';

const values: AppEnvironment = {
  NODE_ENV: 'test',
  PORT: 3000,
  TRUST_PROXY_HOPS: 0,
  LOG_LEVEL: 'silent',
  CORS_ORIGINS: '',
  DATABASE_REQUIRED: false,
  DATABASE_MAX_CONNECTIONS: 10,
  DATABASE_CONNECTION_TIMEOUT_MS: 3000,
  DATABASE_STATEMENT_TIMEOUT_MS: 10000,
  TIDE_DATABASE_URL: undefined,
  SHIWEN_READ_DATABASE_URL: undefined,
  SHIWEN_READ_MODE: 'VIEWS',
  SHIWEN_ALLOW_TIDE_FIXTURE_FALLBACK: false,
  SHIWEN_TEACHER_IDENTITY_VIEW: undefined,
  PUBLIC_APP_URL: 'http://localhost:5173',
  ALLOWED_EMAIL_DOMAINS: '',
  EMAIL_VERIFICATION_TTL_MINUTES: 60,
  MAIL_DELIVERY_PROVIDER: 'UNAVAILABLE',
  MAIL_API_URL: undefined,
  MAIL_API_ACCESS_KEY: undefined,
  MAIL_API_USER_ID: 1,
  MAIL_API_APP_NAME: 'email',
  MAIL_API_USER_TYPE: 1,
  MAIL_API_LANG: 'arr',
  MAIL_API_TIMEOUT_MS: 10_000,
  MAIL_VERIFY_TEMPLATE_ID: 'verify-email',
  DATA_HASH_SECRET: undefined,
  AUTH_JWT_SECRET: undefined,
  ACCESS_TOKEN_TTL_SECONDS: 900,
  REFRESH_TOKEN_TTL_DAYS: 7,
  PASSWORD_RESET_TTL_MINUTES: 30,
  MAIL_PASSWORD_RESET_TEMPLATE_ID: 'reset-password',
  PUBLIC_API_URL: 'http://localhost:3000',
  FILE_STORAGE_PROVIDER: 'LOCAL',
  LOCAL_FILE_STORAGE_DIR: './storage/private',
  OSS_TIMEOUT_MS: 30_000,
  FILE_UPLOAD_MAX_BYTES: 10_485_760,
  MULTIPART_UPLOAD_MAX_CONCURRENCY: 4,
  FILE_UPLOAD_TTL_MINUTES: 15,
  FILE_ALLOWED_MIME_TYPES: 'image/jpeg,image/png,image/webp,application/pdf',
  BACKGROUND_JOBS_ENABLED: true,
  BACKGROUND_JOB_LEASE_MS: 180_000,
  TEACHER_PHOTO_WORKER_POLL_INTERVAL_MS: 1_000,
  TEACHER_PHOTO_WORKER_BATCH_SIZE: 2,
  TEACHER_PHOTO_WORKER_LEASE_MS: 120_000,
  SYSTEM_NOTIFICATION_PUBLISHER_ENABLED: false,
  SYSTEM_NOTIFICATION_CONFIG_PATH: './config/system-notifications.json',
  SYSTEM_NOTIFICATION_POLL_INTERVAL_MS: 30000,
  SYSTEM_NOTIFICATION_BATCH_SIZE: 20,
  PERSONALIZED_TASK_NOTIFICATION_SCHEDULER_ENABLED: false,
  PERSONALIZED_TASK_NOTIFICATION_ROLLOUT_AT: undefined,
  PERSONALIZED_TASK_NOTIFICATION_POLL_INTERVAL_MS: 300_000,
  GROWTH_STAGE_NOTIFICATION_SCHEDULER_ENABLED: false,
  GROWTH_STAGE_NOTIFICATION_POLL_INTERVAL_MS: 300_000,
  SUPPORT_TICKET_CLEANUP_POLL_INTERVAL_MS: 60_000,
  SUPPORT_TICKET_CLEANUP_BATCH_SIZE: 20,
  AI_GATEWAY_ENABLED: true,
  AI_GATEWAY_CHAT_URL: 'https://gateway.example/chat',
  AI_GATEWAY_UPLOAD_URL: 'https://gateway.example/upload',
  AI_GATEWAY_API_KEY: 'secret-key',
  AI_GATEWAY_PROVIDER: 'VOLCENGINE',
  AI_GATEWAY_MODEL: 'doubao-seed-2-0-lite',
  AI_GATEWAY_BIZ_ID: 'biz-id',
  AI_GATEWAY_BIZ_TYPE: 'biz-type',
  AI_GATEWAY_UPLOAD_BUCKET: 'private-bucket',
  AI_GATEWAY_UPLOAD_PROVIDER: 'GOOGLE',
  AI_GATEWAY_TIMEOUT_MS: 30000,
};

function makeClient(overrides: Partial<AppEnvironment> = {}) {
  const config = {
    get: jest.fn(
      (key: keyof AppEnvironment) => ({ ...values, ...overrides })[key],
    ),
  } as unknown as ConfigService<AppEnvironment, true>;
  return new CompanyAiGatewayClient(config);
}

describe('CompanyAiGatewayClient', () => {
  afterEach(() => jest.restoreAllMocks());

  it('sends images inline because the Volcengine model rejects gs:// references', async () => {
    const fetchMock = jest.spyOn(global, 'fetch').mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          success: true,
          res: {
            id: 'gateway-run',
            choices: [{ message: { content: '{"decision":"PASS"}' } }],
            usage: { prompt_tokens: 12, completion_tokens: 3 },
          },
        }),
        { status: 200 },
      ),
    );

    await expect(
      makeClient().complete({
        systemPrompt: 'configured prompt',
        userText: 'teacher evidence',
        file: {
          content: Buffer.from('image'),
          filename: 'evidence.png',
          mimeType: 'image/png',
        },
      }),
    ).resolves.toEqual({
      content: '{"decision":"PASS"}',
      gatewayRef: 'gateway-run',
      inputUnits: 12,
      outputUnits: 3,
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const requestBody = (fetchMock.mock.calls[0][1] as RequestInit).body;
    expect(typeof requestBody).toBe('string');
    const chatBody = JSON.parse(requestBody as string) as Record<
      string,
      unknown
    >;
    expect(chatBody).toMatchObject({
      api_key: 'secret-key',
      provider: 'VOLCENGINE',
      model: 'doubao-seed-2-0-lite',
    });
    expect(JSON.stringify(chatBody)).toContain(
      'data:image/png;base64,aW1hZ2U=',
    );
  });

  it('keeps the upload-reference flow for non-image files', async () => {
    const fetchMock = jest
      .spyOn(global, 'fetch')
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            success: true,
            res: { file_url: 'gs://safe/file' },
          }),
          { status: 200 },
        ),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            success: true,
            res: {
              id: 'gateway-run',
              choices: [{ message: { content: '{"decision":"PASS"}' } }],
              usage: { prompt_tokens: 12, completion_tokens: 3 },
            },
          }),
          { status: 200 },
        ),
      );

    await makeClient().complete({
      systemPrompt: 'configured prompt',
      userText: 'teacher evidence',
      file: {
        content: Buffer.from('video'),
        filename: 'evidence.mp4',
        mimeType: 'video/mp4',
      },
    });

    expect(fetchMock).toHaveBeenCalledTimes(2);
    const requestBody = (fetchMock.mock.calls[1][1] as RequestInit).body;
    expect(JSON.stringify(JSON.parse(requestBody as string))).toContain(
      'gs://safe/file',
    );
  });

  it('fails closed when the gateway is disabled', async () => {
    await expect(
      makeClient({ AI_GATEWAY_ENABLED: false }).complete({
        systemPrompt: 'prompt',
        userText: 'question',
      }),
    ).rejects.toEqual(new AiGatewayError('AI_GATEWAY_DISABLED'));
  });
});
