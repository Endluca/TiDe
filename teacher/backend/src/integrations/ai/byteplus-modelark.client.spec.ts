import type { ConfigService } from '@nestjs/config';
import type { AppEnvironment } from '../../platform/config/environment';
import { AiGatewayError } from './ai-gateway.models';
import { BytePlusModelArkClient } from './byteplus-modelark.client';

const values: AppEnvironment = {
  NODE_ENV: 'test',
  BIND_HOST: '0.0.0.0',
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
  KUOZHI_COURSE_CONFIG_PATH: './config/kuozhi-courses.json',
  KUOZHI_DETAIL_URL: 'http://edu.51talk.me/api/me/TeacherCourseDetail',
  KUOZHI_DETAIL_TIMEOUT_MS: 8_000,
  KUOZHI_DETAIL_RETRY_COUNT: 1,
  FILE_STORAGE_PROVIDER: 'LOCAL',
  LOCAL_FILE_STORAGE_DIR: './storage/private',
  OSS_TIMEOUT_MS: 30_000,
  FILE_UPLOAD_MAX_BYTES: 10_485_760,
  MULTIPART_UPLOAD_MAX_CONCURRENCY: 4,
  FILE_UPLOAD_TTL_MINUTES: 15,
  FILE_ALLOWED_MIME_TYPES: 'image/jpeg,image/png,image/webp,application/pdf',
  BACKGROUND_JOBS_ENABLED: true,
  BACKGROUND_JOB_LEASE_MS: 180_000,
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
  MODELARK_ENABLED: true,
  MODELARK_BASE_URL: 'https://ark.ap-southeast.bytepluses.com/api/v3',
  ARK_API_KEY: 'secret-key',
  MODELARK_MODEL: 'seed-2-0-lite-260228',
  MODELARK_TIMEOUT_MS: 30000,
};

function makeClient(overrides: Partial<AppEnvironment> = {}) {
  const config = {
    get: jest.fn(
      (key: keyof AppEnvironment) => ({ ...values, ...overrides })[key],
    ),
  } as unknown as ConfigService<AppEnvironment, true>;
  return new BytePlusModelArkClient(config);
}

function responseBody(content: string) {
  return {
    id: 'resp-modelark-run',
    object: 'response',
    output: [
      {
        id: 'message-1',
        type: 'message',
        role: 'assistant',
        status: 'completed',
        content: [
          {
            type: 'output_text',
            text: content,
            annotations: [],
          },
        ],
      },
    ],
    usage: { input_tokens: 12, output_tokens: 3, total_tokens: 15 },
  };
}

async function requestDetails(fetchMock: jest.SpiedFunction<typeof fetch>) {
  const [request, init] = fetchMock.mock.calls[0];
  const url = request instanceof Request ? request.url : String(request);
  const headers = new Headers(
    init?.headers ?? (request instanceof Request ? request.headers : undefined),
  );
  const body =
    typeof init?.body === 'string'
      ? init.body
      : request instanceof Request
        ? await request.clone().text()
        : '';
  return { url, headers, body: JSON.parse(body) as Record<string, unknown> };
}

describe('BytePlusModelArkClient', () => {
  afterEach(() => jest.restoreAllMocks());

  it('calls the BytePlus Responses API with a private inline image', async () => {
    const fetchMock = jest.spyOn(global, 'fetch').mockResolvedValueOnce(
      new Response(JSON.stringify(responseBody('{"decision":"PASS"}')), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      }),
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
      gatewayRef: 'resp-modelark-run',
      inputUnits: 12,
      outputUnits: 3,
    });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const request = await requestDetails(fetchMock);
    expect(request.url).toBe(
      'https://ark.ap-southeast.bytepluses.com/api/v3/responses',
    );
    expect(request.headers.get('authorization')).toBe('Bearer secret-key');
    expect(request.body).toMatchObject({
      model: 'seed-2-0-lite-260228',
      store: false,
      stream: false,
      temperature: 0,
      thinking: { type: 'disabled' },
      text: { format: { type: 'json_object' } },
    });
    expect(JSON.stringify(request.body)).toContain(
      'data:image/png;base64,aW1hZ2U=',
    );
    expect(JSON.stringify(request.body)).not.toContain('secret-key');
  });

  it('supports text-only FAQ calls without adding a file input', async () => {
    const fetchMock = jest.spyOn(global, 'fetch').mockResolvedValueOnce(
      new Response(JSON.stringify(responseBody('{"decision":"MATCHED"}')), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      }),
    );

    await makeClient().complete({
      systemPrompt: 'faq prompt',
      userText: 'faq question',
    });

    const request = await requestDetails(fetchMock);
    expect(JSON.stringify(request.body)).toContain('faq question');
    expect(JSON.stringify(request.body)).not.toContain('input_image');
    expect(JSON.stringify(request.body)).not.toContain('input_file');
  });

  it('fails closed when ModelArk is disabled', async () => {
    await expect(
      makeClient({ MODELARK_ENABLED: false }).complete({
        systemPrompt: 'prompt',
        userText: 'question',
      }),
    ).rejects.toEqual(new AiGatewayError('AI_GATEWAY_DISABLED'));
  });

  it('maps provider HTTP errors to the stable gateway error contract', async () => {
    jest.spyOn(global, 'fetch').mockResolvedValueOnce(
      new Response(JSON.stringify({ error: { message: 'rejected' } }), {
        status: 401,
        headers: { 'content-type': 'application/json' },
      }),
    );

    await expect(
      makeClient().complete({
        systemPrompt: 'prompt',
        userText: 'question',
      }),
    ).rejects.toEqual(new AiGatewayError('AI_GATEWAY_HTTP_ERROR'));
  });
});
