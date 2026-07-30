import { z } from 'zod';

const optionalPostgresUrl = z.preprocess(
  (value) => (value === '' ? undefined : value),
  z
    .string()
    .refine(
      (value) =>
        value.startsWith('postgresql://') || value.startsWith('postgres://'),
      '必须使用 postgres:// 或 postgresql://',
    )
    .optional(),
);

const optionalString = z.preprocess(
  (value) => (value === '' ? undefined : value),
  z.string().min(1).optional(),
);

const optionalUrl = z.preprocess(
  (value) => (value === '' ? undefined : value),
  z.string().url().optional(),
);

const optionalIsoDateTime = z.preprocess(
  (value) => (value === '' ? undefined : value),
  z.string().datetime({ offset: true }).optional(),
);

const booleanFromEnvironment = z
  .enum(['true', 'false'])
  .default('false')
  .transform((value) => value === 'true');

const booleanDefaultTrueFromEnvironment = z
  .enum(['true', 'false'])
  .default('true')
  .transform((value) => value === 'true');

const optionalQualifiedViewName = z.preprocess(
  (value) => (value === '' ? undefined : value),
  z
    .string()
    .regex(
      /^[a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*$/,
      '必须使用安全的 schema.view 格式',
    )
    .optional(),
);

function isHttpsUrl(value: string): boolean {
  try {
    return new URL(value).protocol === 'https:';
  } catch {
    return false;
  }
}

function hasStrictPostgresSslMode(value: string): boolean {
  try {
    const url = new URL(value);
    const sslModes = url.searchParams.getAll('sslmode');
    return (
      sslModes.length === 1 &&
      sslModes[0] === 'verify-full' &&
      !url.searchParams.has('ssl')
    );
  } catch {
    return false;
  }
}

export const environmentSchema = z
  .object({
    NODE_ENV: z
      .enum(['development', 'test', 'production'])
      .default('development'),
    PORT: z.coerce.number().int().min(1).max(65535).default(3000),
    TRUST_PROXY_HOPS: z.coerce.number().int().min(0).max(3).default(0),
    LOG_LEVEL: z
      .enum(['fatal', 'error', 'warn', 'info', 'debug', 'trace', 'silent'])
      .default('info'),
    CORS_ORIGINS: z.string().default('http://localhost:5173'),
    DATABASE_REQUIRED: booleanFromEnvironment,
    DATABASE_MAX_CONNECTIONS: z.coerce
      .number()
      .int()
      .min(1)
      .max(50)
      .default(10),
    DATABASE_CONNECTION_TIMEOUT_MS: z.coerce
      .number()
      .int()
      .min(100)
      .max(30_000)
      .default(3_000),
    DATABASE_STATEMENT_TIMEOUT_MS: z.coerce
      .number()
      .int()
      .min(100)
      .max(120_000)
      .default(10_000),
    TIDE_DATABASE_URL: optionalPostgresUrl,
    SHIWEN_READ_DATABASE_URL: optionalPostgresUrl,
    SHIWEN_READ_MODE: z.enum(['VIEWS', 'DIRECT_TABLES']).default('VIEWS'),
    SHIWEN_ALLOW_TIDE_FIXTURE_FALLBACK: booleanFromEnvironment,
    SHIWEN_TEACHER_IDENTITY_VIEW: optionalQualifiedViewName,
    PUBLIC_APP_URL: z.string().url().default('http://localhost:5173'),
    ALLOWED_EMAIL_DOMAINS: z.string().default(''),
    EMAIL_VERIFICATION_TTL_MINUTES: z.coerce
      .number()
      .int()
      .min(5)
      .max(24 * 60)
      .default(60),
    MAIL_DELIVERY_PROVIDER: z
      .enum(['UNAVAILABLE', 'COMPANY_MESSAGE_API'])
      .default('UNAVAILABLE'),
    MAIL_API_URL: optionalUrl,
    MAIL_API_ACCESS_KEY: optionalString,
    MAIL_API_USER_ID: z.coerce.number().int().min(1).default(1),
    MAIL_API_APP_NAME: z.string().min(1).default('email'),
    MAIL_API_USER_TYPE: z.coerce.number().int().min(1).default(1),
    MAIL_API_LANG: z.string().min(1).default('arr'),
    MAIL_API_TIMEOUT_MS: z.coerce
      .number()
      .int()
      .min(1_000)
      .max(120_000)
      .default(10_000),
    MAIL_VERIFY_TEMPLATE_ID: z.string().min(1).default('verify-email'),
    DATA_HASH_SECRET: z.preprocess(
      (value) => (value === '' ? undefined : value),
      z.string().min(32).optional(),
    ),
    AUTH_JWT_SECRET: z.preprocess(
      (value) => (value === '' ? undefined : value),
      z.string().min(32).optional(),
    ),
    ACCESS_TOKEN_TTL_SECONDS: z.coerce
      .number()
      .int()
      .min(60)
      .max(3_600)
      .default(900),
    REFRESH_TOKEN_TTL_DAYS: z.coerce.number().int().min(1).max(30).default(7),
    PASSWORD_RESET_TTL_MINUTES: z.coerce
      .number()
      .int()
      .min(5)
      .max(24 * 60)
      .default(30),
    MAIL_PASSWORD_RESET_TEMPLATE_ID: z
      .string()
      .min(1)
      .default('reset-password'),
    PUBLIC_API_URL: z.string().url().default('http://localhost:3000'),
    FILE_STORAGE_PROVIDER: z.enum(['LOCAL', 'OSS']).default('LOCAL'),
    LOCAL_FILE_STORAGE_DIR: z.string().min(1).default('./storage/private'),
    OSS_REGION: optionalString,
    OSS_ENDPOINT: optionalUrl,
    OSS_BUCKET: optionalString,
    OSS_ACCESS_KEY_ID: optionalString,
    OSS_ACCESS_KEY_SECRET: optionalString,
    OSS_TIMEOUT_MS: z.coerce
      .number()
      .int()
      .min(1_000)
      .max(120_000)
      .default(30_000),
    FILE_UPLOAD_MAX_BYTES: z.coerce
      .number()
      .int()
      .min(1_024)
      .max(50 * 1024 * 1024)
      .default(10 * 1024 * 1024),
    MULTIPART_UPLOAD_MAX_CONCURRENCY: z.coerce
      .number()
      .int()
      .min(1)
      .max(16)
      .default(4),
    FILE_UPLOAD_TTL_MINUTES: z.coerce.number().int().min(5).max(60).default(15),
    FILE_ALLOWED_MIME_TYPES: z
      .string()
      .default('image/jpeg,image/png,image/webp,application/pdf'),
    BACKGROUND_JOBS_ENABLED: booleanDefaultTrueFromEnvironment,
    BACKGROUND_JOB_LEASE_MS: z.coerce
      .number()
      .int()
      .min(30_000)
      .max(3_600_000)
      .default(180_000),
    TEACHER_PHOTO_WORKER_POLL_INTERVAL_MS: z.coerce
      .number()
      .int()
      .min(250)
      .max(60_000)
      .default(1_000),
    TEACHER_PHOTO_WORKER_BATCH_SIZE: z.coerce
      .number()
      .int()
      .min(1)
      .max(10)
      .default(2),
    TEACHER_PHOTO_WORKER_LEASE_MS: z.coerce
      .number()
      .int()
      .min(30_000)
      .max(600_000)
      .default(120_000),
    SYSTEM_NOTIFICATION_PUBLISHER_ENABLED: booleanFromEnvironment,
    SYSTEM_NOTIFICATION_CONFIG_PATH: z
      .string()
      .min(1)
      .default('./config/system-notifications.json'),
    SYSTEM_NOTIFICATION_POLL_INTERVAL_MS: z.coerce
      .number()
      .int()
      .min(10_000)
      .max(3_600_000)
      .default(60_000),
    SYSTEM_NOTIFICATION_BATCH_SIZE: z.coerce
      .number()
      .int()
      .min(1)
      .max(100)
      .default(20),
    PERSONALIZED_TASK_NOTIFICATION_SCHEDULER_ENABLED: booleanFromEnvironment,
    PERSONALIZED_TASK_NOTIFICATION_ROLLOUT_AT: optionalIsoDateTime,
    PERSONALIZED_TASK_NOTIFICATION_POLL_INTERVAL_MS: z.coerce
      .number()
      .int()
      .min(10_000)
      .max(3_600_000)
      .default(300_000),
    GROWTH_STAGE_NOTIFICATION_SCHEDULER_ENABLED: booleanFromEnvironment,
    GROWTH_STAGE_NOTIFICATION_POLL_INTERVAL_MS: z.coerce
      .number()
      .int()
      .min(10_000)
      .max(3_600_000)
      .default(300_000),
    SUPPORT_TICKET_CLEANUP_POLL_INTERVAL_MS: z.coerce
      .number()
      .int()
      .min(10_000)
      .max(3_600_000)
      .default(60_000),
    SUPPORT_TICKET_CLEANUP_BATCH_SIZE: z.coerce
      .number()
      .int()
      .min(1)
      .max(100)
      .default(20),
    AI_GATEWAY_ENABLED: booleanFromEnvironment,
    AI_GATEWAY_CHAT_URL: z
      .string()
      .url()
      .default('https://aigateway.51talk.com/v1/chat/completions'),
    AI_GATEWAY_UPLOAD_URL: z
      .string()
      .url()
      .default('https://aigateway.51talk.com/v1/task/sync'),
    AI_GATEWAY_API_KEY: z.preprocess(
      (value) => (value === '' ? undefined : value),
      z.string().min(1).optional(),
    ),
    AI_GATEWAY_PROVIDER: z.string().min(1).default('VOLCENGINE'),
    AI_GATEWAY_MODEL: z.string().min(1).default('doubao-seed-2-0-lite'),
    AI_GATEWAY_BIZ_ID: z.string().min(1).default('8218469790818477355'),
    AI_GATEWAY_BIZ_TYPE: z.string().min(1).default('NTT_CE_PRS'),
    AI_GATEWAY_UPLOAD_BUCKET: z.string().min(1).default('ai-efficiency-center'),
    AI_GATEWAY_UPLOAD_PROVIDER: z.string().min(1).default('GOOGLE'),
    AI_GATEWAY_TIMEOUT_MS: z.coerce
      .number()
      .int()
      .min(1_000)
      .max(120_000)
      .default(30_000),
  })
  .superRefine((environment, context) => {
    const tideDatabaseRequired =
      environment.DATABASE_REQUIRED || environment.NODE_ENV === 'production';

    if (tideDatabaseRequired && !environment.TIDE_DATABASE_URL) {
      context.addIssue({
        code: 'custom',
        path: ['TIDE_DATABASE_URL'],
        message: '生产环境或 DATABASE_REQUIRED=true 时不能为空',
      });
    }

    if (
      environment.NODE_ENV === 'production' &&
      !environment.SHIWEN_READ_DATABASE_URL
    ) {
      context.addIssue({
        code: 'custom',
        path: ['SHIWEN_READ_DATABASE_URL'],
        message: '生产环境不能为空',
      });
    }

    if (environment.NODE_ENV === 'production') {
      if (environment.TRUST_PROXY_HOPS !== 1) {
        context.addIssue({
          code: 'custom',
          path: ['TRUST_PROXY_HOPS'],
          message: '联合生产拓扑只允许信任已清洗转发头的 Edge 一跳',
        });
      }

      if (
        environment.SHIWEN_READ_MODE === 'VIEWS' &&
        !environment.SHIWEN_TEACHER_IDENTITY_VIEW
      ) {
        context.addIssue({
          code: 'custom',
          path: ['SHIWEN_TEACHER_IDENTITY_VIEW'],
          message: '生产 VIEWS 模式必须配置教师身份只读视图',
        });
      }

      const databaseUrls = [
        ['TIDE_DATABASE_URL', environment.TIDE_DATABASE_URL],
        ['SHIWEN_READ_DATABASE_URL', environment.SHIWEN_READ_DATABASE_URL],
      ] as const;
      for (const [setting, value] of databaseUrls) {
        if (value && !hasStrictPostgresSslMode(value)) {
          context.addIssue({
            code: 'custom',
            path: [setting],
            message: '生产环境必须且只能配置 sslmode=verify-full',
          });
        }
      }

      const publicUrls = [
        ['PUBLIC_APP_URL', environment.PUBLIC_APP_URL],
        ['PUBLIC_API_URL', environment.PUBLIC_API_URL],
      ] as const;
      for (const [setting, value] of publicUrls) {
        if (!isHttpsUrl(value)) {
          context.addIssue({
            code: 'custom',
            path: [setting],
            message: '生产环境必须使用 HTTPS',
          });
        }
      }

      if (environment.FILE_STORAGE_PROVIDER !== 'OSS') {
        context.addIssue({
          code: 'custom',
          path: ['FILE_STORAGE_PROVIDER'],
          message: '生产环境必须使用 OSS，不允许使用本地文件存储',
        });
      }
    }

    if (
      environment.NODE_ENV === 'production' &&
      !environment.DATA_HASH_SECRET
    ) {
      context.addIssue({
        code: 'custom',
        path: ['DATA_HASH_SECRET'],
        message: '生产环境不能为空且至少 32 个字符',
      });
    }

    if (environment.NODE_ENV === 'production' && !environment.AUTH_JWT_SECRET) {
      context.addIssue({
        code: 'custom',
        path: ['AUTH_JWT_SECRET'],
        message: '生产环境不能为空且至少 32 个字符',
      });
    }

    if (environment.AI_GATEWAY_ENABLED && !environment.AI_GATEWAY_API_KEY) {
      context.addIssue({
        code: 'custom',
        path: ['AI_GATEWAY_API_KEY'],
        message: 'AI_GATEWAY_ENABLED=true 时不能为空',
      });
    }

    if (environment.MAIL_DELIVERY_PROVIDER === 'COMPANY_MESSAGE_API') {
      const requiredSettings = ['MAIL_API_URL', 'MAIL_API_ACCESS_KEY'] as const;
      for (const setting of requiredSettings) {
        if (!environment[setting]) {
          context.addIssue({
            code: 'custom',
            path: [setting],
            message: '启用公司邮件接口时不能为空',
          });
        }
      }
    }

    if (
      environment.PERSONALIZED_TASK_NOTIFICATION_SCHEDULER_ENABLED &&
      !environment.PERSONALIZED_TASK_NOTIFICATION_ROLLOUT_AT
    ) {
      context.addIssue({
        code: 'custom',
        path: ['PERSONALIZED_TASK_NOTIFICATION_ROLLOUT_AT'],
        message:
          'PERSONALIZED_TASK_NOTIFICATION_SCHEDULER_ENABLED=true 时必须设置明确 rollout 时间',
      });
    }

    if (environment.FILE_STORAGE_PROVIDER === 'OSS') {
      const requiredSettings = [
        'OSS_REGION',
        'OSS_ENDPOINT',
        'OSS_BUCKET',
        'OSS_ACCESS_KEY_ID',
        'OSS_ACCESS_KEY_SECRET',
      ] as const;
      for (const setting of requiredSettings) {
        if (!environment[setting]) {
          context.addIssue({
            code: 'custom',
            path: [setting],
            message: 'FILE_STORAGE_PROVIDER=OSS 时不能为空',
          });
        }
      }
    }
  });

export type AppEnvironment = z.infer<typeof environmentSchema>;

function withCompanyTestDatabase(
  input: Record<string, unknown>,
): Record<string, unknown> {
  if (input.COMPANY_TEST_DATABASE_ENABLED !== 'true') return input;

  const requiredKeys = [
    'TIDE_ADMIN_DB_HOST',
    'TIDE_ADMIN_DB_PORT',
    'TIDE_ADMIN_DB_NAME',
    'TIDE_APP_DB_USER',
    'TIDE_APP_DB_PASSWORD',
  ] as const;
  for (const key of requiredKeys) {
    if (typeof input[key] !== 'string' || input[key].length === 0) {
      throw new Error(`环境配置无效：${key}: 公司测试库配置不能为空`);
    }
  }

  const host = String(input.TIDE_ADMIN_DB_HOST);
  const port = String(input.TIDE_ADMIN_DB_PORT);
  const database = encodeURIComponent(String(input.TIDE_ADMIN_DB_NAME));
  const user = encodeURIComponent(String(input.TIDE_APP_DB_USER));
  const password = encodeURIComponent(String(input.TIDE_APP_DB_PASSWORD));
  const sslMode = encodeURIComponent(
    typeof input.TIDE_ADMIN_DB_SSLMODE === 'string'
      ? input.TIDE_ADMIN_DB_SSLMODE
      : 'disable',
  );
  const connectionString =
    `postgresql://${user}:${password}@${host}:${port}/${database}` +
    `?sslmode=${sslMode}`;

  return {
    ...input,
    TIDE_DATABASE_URL: connectionString,
    SHIWEN_READ_DATABASE_URL: connectionString,
  };
}

export function validateEnvironment(
  input: Record<string, unknown>,
): AppEnvironment {
  const result = environmentSchema.safeParse(withCompanyTestDatabase(input));

  if (!result.success) {
    const issues = result.error.issues
      .map((issue) => `${issue.path.join('.')}: ${issue.message}`)
      .join('; ');
    throw new Error(`环境配置无效：${issues}`);
  }

  return result.data;
}

export function parseCorsOrigins(value: string): string[] {
  return value
    .split(',')
    .map((origin) => origin.trim())
    .filter(Boolean);
}

export function parseAllowedEmailDomains(value: string): string[] {
  return value
    .split(',')
    .map((domain) => domain.trim().toLowerCase())
    .filter(Boolean);
}
