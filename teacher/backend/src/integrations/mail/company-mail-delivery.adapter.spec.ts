import { ConfigService } from '@nestjs/config';
import type { AppEnvironment } from '../../platform/config/environment';
import { DependencyHealthRegistry } from '../../platform/observability/dependency-health.registry';
import {
  CompanyMailDeliveryAdapter,
  CompanyMailDeliveryError,
} from './company-mail-delivery.adapter';

describe('CompanyMailDeliveryAdapter', () => {
  const config = new ConfigService<AppEnvironment, true>({
    MAIL_API_URL: 'http://mail.example.test/api/v1/msg/send',
    MAIL_API_ACCESS_KEY: 'test-access-key',
    MAIL_API_USER_ID: 1,
    MAIL_API_APP_NAME: 'email',
    MAIL_API_USER_TYPE: 1,
    MAIL_API_LANG: 'arr',
    MAIL_API_TIMEOUT_MS: 10_000,
  });

  afterEach(() => {
    jest.restoreAllMocks();
  });

  it('maps a verification link to the configured company template variables', async () => {
    const fetchMock = jest.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ request_id: 'mail-request-001' }), {
        status: 200,
      }),
    );
    const health = new DependencyHealthRegistry();
    const mail = new CompanyMailDeliveryAdapter(config, health);

    await expect(
      mail.sendVerificationEmail({
        email: 'teacher@example.com',
        verificationUrl: 'https://camp.example.com/verify-email?token=secret',
        expiresInMinutes: 60,
        templateId: 'teacher-camp-email',
      }),
    ).resolves.toBe('mail-request-001');

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const request = fetchMock.mock.calls[0][1];
    expect(typeof request?.body).toBe('string');
    const payload = JSON.parse(request?.body as string) as Record<
      string,
      unknown
    >;
    expect(payload).toMatchObject({
      access_key: 'test-access-key',
      user_id: 1,
      app_name: 'email',
      template_id: 'teacher-camp-email',
      user_type: 1,
      lang: 'arr',
    });
    expect(JSON.parse(String(payload.data))).toEqual({
      reset_url: 'https://camp.example.com/verify-email?token=secret',
      expires_in_minutes: '60',
      email: 'teacher@example.com',
    });
    expect(health.snapshot('mail', 'configured')).toMatchObject({
      status: 'ok',
      errorCode: null,
    });
  });

  it('maps the password reset link through the same company API contract', async () => {
    const fetchMock = jest
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(new Response('', { status: 200 }));
    const mail = new CompanyMailDeliveryAdapter(config);

    await expect(
      mail.sendPasswordResetEmail({
        email: 'teacher@example.com',
        resetUrl: 'https://camp.example.com/reset-password?token=secret',
        expiresInMinutes: 30,
        templateId: 'teacher-camp-email',
      }),
    ).resolves.toBeNull();

    const request = fetchMock.mock.calls[0][1];
    expect(typeof request?.body).toBe('string');
    const payload = JSON.parse(request?.body as string) as Record<
      string,
      unknown
    >;
    expect(JSON.parse(String(payload.data))).toMatchObject({
      reset_url: 'https://camp.example.com/reset-password?token=secret',
      expires_in_minutes: '30',
    });
  });

  it('records an HTTP failure without exposing the provider response', async () => {
    jest
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(new Response('denied', { status: 403 }));
    const health = new DependencyHealthRegistry();
    const mail = new CompanyMailDeliveryAdapter(config, health);

    await expect(
      mail.sendVerificationEmail({
        email: 'teacher@example.com',
        verificationUrl: 'https://camp.example.com/verify-email?token=secret',
        expiresInMinutes: 60,
        templateId: 'teacher-camp-email',
      }),
    ).rejects.toBeInstanceOf(CompanyMailDeliveryError);
    expect(health.snapshot('mail', 'configured')).toMatchObject({
      status: 'error',
      errorCode: 'MAIL_DELIVERY_HTTP_403',
    });
  });
});
