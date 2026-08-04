import { parseCorsOrigins, validateEnvironment } from './environment';

const validProductionEnvironment = {
  NODE_ENV: 'production',
  TRUST_PROXY_HOPS: '1',
  SHIWEN_READ_MODE: 'DIRECT_TABLES',
  TIDE_DATABASE_URL:
    'postgresql://tide_app:password@db.example.test/tide?sslmode=verify-full',
  SHIWEN_READ_DATABASE_URL:
    'postgresql://shiwen_read:password@db.example.test/tide?sslmode=verify-full',
  PUBLIC_APP_URL: 'https://teacher.example.test',
  PUBLIC_API_URL: 'https://teacher.example.test',
  FILE_STORAGE_PROVIDER: 'OSS',
  OSS_REGION: 'oss-ap-southeast-1',
  OSS_ENDPOINT: 'https://oss-ap-southeast-1.aliyuncs.com',
  OSS_BUCKET: 'tide-production',
  OSS_ACCESS_KEY_ID: 'test-access-key-id',
  OSS_ACCESS_KEY_SECRET: 'test-access-key-secret',
  DATA_HASH_SECRET: 'data-hash-secret-with-at-least-32-characters',
  AUTH_JWT_SECRET: 'auth-jwt-secret-with-at-least-32-characters',
} as const;

describe('environment configuration', () => {
  it('normalizes defaults and comma-separated CORS origins', () => {
    const environment = validateEnvironment({ NODE_ENV: 'test' });

    expect(environment.BIND_HOST).toBe('0.0.0.0');
    expect(environment.PORT).toBe(3000);
    expect(environment.TRUST_PROXY_HOPS).toBe(0);
    expect(environment.DATABASE_REQUIRED).toBe(false);
    expect(environment.FILE_UPLOAD_MAX_BYTES).toBe(10 * 1024 * 1024);
    expect(environment.MULTIPART_UPLOAD_MAX_CONCURRENCY).toBe(4);
    expect(environment.FILE_STORAGE_PROVIDER).toBe('LOCAL');
    expect(environment.LOCAL_FILE_STORAGE_DIR).toBe('./storage/private');
    expect(environment.MAIL_DELIVERY_PROVIDER).toBe('UNAVAILABLE');
    expect(environment.AI_GATEWAY_ENABLED).toBe(false);
    expect(environment.BACKGROUND_JOBS_ENABLED).toBe(true);
    expect(environment.BACKGROUND_JOB_LEASE_MS).toBe(180_000);
    expect(environment.TEACHER_PHOTO_WORKER_POLL_INTERVAL_MS).toBe(1_000);
    expect(environment.TEACHER_PHOTO_WORKER_BATCH_SIZE).toBe(2);
    expect(environment.TEACHER_PHOTO_WORKER_LEASE_MS).toBe(120_000);
    expect(environment.PERSONALIZED_TASK_NOTIFICATION_SCHEDULER_ENABLED).toBe(
      false,
    );
    expect(environment.PERSONALIZED_TASK_NOTIFICATION_POLL_INTERVAL_MS).toBe(
      300_000,
    );
    expect(environment.AI_GATEWAY_PROVIDER).toBe('VOLCENGINE');
    expect(environment.AI_GATEWAY_MODEL).toBe('doubao-seed-2-0-lite');
    expect(parseCorsOrigins('https://a.example, https://b.example')).toEqual([
      'https://a.example',
      'https://b.example',
    ]);
  });

  it('only accepts explicit wildcard or loopback bind hosts', () => {
    expect(
      validateEnvironment({
        NODE_ENV: 'test',
        BIND_HOST: '127.0.0.1',
      }).BIND_HOST,
    ).toBe('127.0.0.1');
    expect(() =>
      validateEnvironment({
        NODE_ENV: 'test',
        BIND_HOST: 'teacher.example.test',
      }),
    ).toThrow('BIND_HOST');
  });

  it('requires a gateway key only when AI is enabled', () => {
    expect(() =>
      validateEnvironment({ NODE_ENV: 'test', AI_GATEWAY_ENABLED: 'true' }),
    ).toThrow('AI_GATEWAY_API_KEY');

    expect(
      validateEnvironment({
        NODE_ENV: 'test',
        AI_GATEWAY_ENABLED: 'true',
        AI_GATEWAY_API_KEY: 'test-key',
      }).AI_GATEWAY_ENABLED,
    ).toBe(true);
  });

  it('requires complete OSS settings only when OSS storage is enabled', () => {
    expect(() =>
      validateEnvironment({
        NODE_ENV: 'test',
        FILE_STORAGE_PROVIDER: 'OSS',
      }),
    ).toThrow('OSS_REGION');

    expect(
      validateEnvironment({
        NODE_ENV: 'test',
        FILE_STORAGE_PROVIDER: 'OSS',
        OSS_REGION: 'oss-ap-southeast-1',
        OSS_ENDPOINT:
          'https://eff-new-teacher-camp.oss-ap-southeast-1.aliyuncs.com',
        OSS_BUCKET: 'eff-new-teacher-camp',
        OSS_ACCESS_KEY_ID: 'test-access-key-id',
        OSS_ACCESS_KEY_SECRET: 'test-access-key-secret',
      }).FILE_STORAGE_PROVIDER,
    ).toBe('OSS');
  });

  it('requires the company mail endpoint and access key when mail is enabled', () => {
    expect(() =>
      validateEnvironment({
        NODE_ENV: 'test',
        MAIL_DELIVERY_PROVIDER: 'COMPANY_MESSAGE_API',
      }),
    ).toThrow('MAIL_API_URL');

    const environment = validateEnvironment({
      NODE_ENV: 'test',
      MAIL_DELIVERY_PROVIDER: 'COMPANY_MESSAGE_API',
      MAIL_API_URL: 'http://mail.example.test/api/v1/msg/send',
      MAIL_API_ACCESS_KEY: 'test-access-key',
    });

    expect(environment.MAIL_DELIVERY_PROVIDER).toBe('COMPANY_MESSAGE_API');
    expect(environment.MAIL_API_APP_NAME).toBe('email');
    expect(environment.MAIL_API_USER_ID).toBe(1);
  });

  it('requires an explicit rollout time only when personalized task reminders are enabled', () => {
    expect(() =>
      validateEnvironment({
        NODE_ENV: 'test',
        PERSONALIZED_TASK_NOTIFICATION_SCHEDULER_ENABLED: 'true',
      }),
    ).toThrow('PERSONALIZED_TASK_NOTIFICATION_ROLLOUT_AT');

    const environment = validateEnvironment({
      NODE_ENV: 'test',
      PERSONALIZED_TASK_NOTIFICATION_SCHEDULER_ENABLED: 'true',
      PERSONALIZED_TASK_NOTIFICATION_ROLLOUT_AT: '2026-07-24T15:00:00+08:00',
    });

    expect(environment.PERSONALIZED_TASK_NOTIFICATION_SCHEDULER_ENABLED).toBe(
      true,
    );
    expect(environment.PERSONALIZED_TASK_NOTIFICATION_ROLLOUT_AT).toBe(
      '2026-07-24T15:00:00+08:00',
    );
  });

  it('requires the TIDE database in production', () => {
    expect(() => validateEnvironment({ NODE_ENV: 'production' })).toThrow(
      'TIDE_DATABASE_URL',
    );
  });

  it('accepts a complete fail-closed production configuration', () => {
    const environment = validateEnvironment(validProductionEnvironment);

    expect(environment.NODE_ENV).toBe('production');
    expect(environment.FILE_STORAGE_PROVIDER).toBe('OSS');
  });

  it('requires the Shiwen read database in production', () => {
    expect(() =>
      validateEnvironment({
        ...validProductionEnvironment,
        SHIWEN_READ_DATABASE_URL: undefined,
      }),
    ).toThrow('SHIWEN_READ_DATABASE_URL');
  });

  it.each(['0', '2', '3'])(
    'requires exactly one sanitized Edge hop in production: %s',
    (trustedProxyHops) => {
      expect(() =>
        validateEnvironment({
          ...validProductionEnvironment,
          TRUST_PROXY_HOPS: trustedProxyHops,
        }),
      ).toThrow('TRUST_PROXY_HOPS');
    },
  );

  it('requires the identity view when production uses VIEWS mode', () => {
    expect(() =>
      validateEnvironment({
        ...validProductionEnvironment,
        SHIWEN_READ_MODE: 'VIEWS',
        SHIWEN_TEACHER_IDENTITY_VIEW: undefined,
      }),
    ).toThrow('SHIWEN_TEACHER_IDENTITY_VIEW');

    expect(
      validateEnvironment({
        ...validProductionEnvironment,
        SHIWEN_READ_MODE: 'VIEWS',
        SHIWEN_TEACHER_IDENTITY_VIEW: 'shiwen.teacher_identity_v1',
      }).SHIWEN_TEACHER_IDENTITY_VIEW,
    ).toBe('shiwen.teacher_identity_v1');
  });

  it.each([
    ['missing sslmode', 'postgresql://tide_app:password@db.example.test/tide'],
    [
      'non-verifying sslmode',
      'postgresql://tide_app:password@db.example.test/tide?sslmode=require',
    ],
    [
      'duplicate sslmode',
      'postgresql://tide_app:password@db.example.test/tide?sslmode=verify-full&sslmode=disable',
    ],
    [
      'conflicting ssl parameter',
      'postgresql://tide_app:password@db.example.test/tide?sslmode=verify-full&ssl=false',
    ],
  ])('rejects %s for a production PostgreSQL URL', (_, databaseUrl) => {
    expect(() =>
      validateEnvironment({
        ...validProductionEnvironment,
        TIDE_DATABASE_URL: databaseUrl,
      }),
    ).toThrow('sslmode=verify-full');
  });

  it.each([
    ['PUBLIC_APP_URL', 'http://teacher.example.test'],
    ['PUBLIC_API_URL', 'http://teacher.example.test'],
  ])('requires HTTPS for production %s', (setting, value) => {
    expect(() =>
      validateEnvironment({
        ...validProductionEnvironment,
        [setting]: value,
      }),
    ).toThrow(`${setting}: 生产环境必须使用 HTTPS`);
  });

  it('rejects local file storage in production', () => {
    expect(() =>
      validateEnvironment({
        ...validProductionEnvironment,
        FILE_STORAGE_PROVIDER: 'LOCAL',
      }),
    ).toThrow('FILE_STORAGE_PROVIDER');
  });

  it('rejects non-PostgreSQL database URLs', () => {
    expect(() =>
      validateEnvironment({
        NODE_ENV: 'test',
        TIDE_DATABASE_URL: 'mysql://localhost/tide',
      }),
    ).toThrow('postgresql://');
  });

  it.each(['0', '17'])(
    'rejects multipart upload concurrency outside 1-16: %s',
    (value) => {
      expect(() =>
        validateEnvironment({
          NODE_ENV: 'test',
          MULTIPART_UPLOAD_MAX_CONCURRENCY: value,
        }),
      ).toThrow('MULTIPART_UPLOAD_MAX_CONCURRENCY');
    },
  );

  it('uses the same company test database for TIDE and Shiwen reads', () => {
    const environment = validateEnvironment({
      NODE_ENV: 'test',
      COMPANY_TEST_DATABASE_ENABLED: 'true',
      TIDE_ADMIN_DB_HOST: 'company-test.example',
      TIDE_ADMIN_DB_PORT: '5432',
      TIDE_ADMIN_DB_NAME: 'growth_test',
      TIDE_APP_DB_USER: 'tit_teacher_crud',
      TIDE_APP_DB_PASSWORD: 'password-with-@',
    });

    expect(environment.TIDE_DATABASE_URL).toBe(
      'postgresql://tit_teacher_crud:password-with-%40@company-test.example:5432/growth_test?sslmode=disable',
    );
    expect(environment.SHIWEN_READ_DATABASE_URL).toBe(
      environment.TIDE_DATABASE_URL,
    );
  });

  it('only accepts qualified safe view names', () => {
    expect(() =>
      validateEnvironment({
        NODE_ENV: 'test',
        SHIWEN_TEACHER_IDENTITY_VIEW: 'unsafe;drop table teachers',
      }),
    ).toThrow('schema.view');

    expect(
      validateEnvironment({
        NODE_ENV: 'test',
        SHIWEN_TEACHER_IDENTITY_VIEW: 'shiwen.teacher_identity_v1',
      }).SHIWEN_TEACHER_IDENTITY_VIEW,
    ).toBe('shiwen.teacher_identity_v1');
  });
});
