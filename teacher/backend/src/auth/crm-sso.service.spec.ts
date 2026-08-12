import {
  ConflictException,
  UnauthorizedException,
  UnprocessableEntityException,
} from '@nestjs/common';
import type { ConfigService } from '@nestjs/config';
import type { ShiwenTeacherReadAdapter } from '../integrations/shiwen/shiwen-read.adapters';
import type { AppEnvironment } from '../platform/config/environment';
import type { AuthModeService } from './auth-mode.service';
import { CrmSsoBindingError } from './crm-sso.models';
import type { CrmSsoRepository } from './crm-sso.repository';
import { CrmSsoService } from './crm-sso.service';
import type { CrmSsoTokenService } from './crm-sso-token.service';
import type { AuthTokenService } from './auth-token.service';
import type { SessionRepository } from './session.repository';
import { CrmSsoSessionAccountInactiveError } from './session.repository';
import type { SessionService } from './session.service';

function createService() {
  const values: Partial<AppEnvironment> = {
    PUBLIC_APP_URL: 'https://tide.example.test',
    ALLOWED_EMAIL_DOMAINS: '51talk.com',
    CRM_SSO_EXCHANGE_TTL_SECONDS: 60,
  };
  const config = {
    get: jest.fn((key: keyof AppEnvironment) => values[key]),
  } as unknown as ConfigService<AppEnvironment, true>;
  const now = Math.floor(Date.now() / 1000);
  const verify = jest.fn().mockResolvedValue({
    iat: now,
    exp: now + 120,
    jti: '0123456789abcdef0123456789abcdef',
    iss: 'crm',
    aud: 'tide',
    teacherId: 'TEACHER-001',
    email: 'teacher@51talk.com',
  });
  const createLogin = jest.fn().mockResolvedValue({
    accountId: 'account-001',
    created: false,
  });
  const findIdentity = jest.fn().mockResolvedValue({
    teacherId: 'TEACHER-001',
    dataMode: 'REAL',
  });
  const recordSecurityEvent = jest.fn().mockResolvedValue(undefined);
  const exchangeCrmSso = jest.fn();
  const authMode = {
    assertCrmSsoEnabled: jest.fn(),
  } as unknown as AuthModeService;
  const tokenService = {
    create: jest.fn().mockReturnValue({
      raw: 'raw-one-time-exchange-code',
      hash: 'a'.repeat(64),
    }),
    hash: jest.fn((value: string) => `hash:${value}`),
  } as unknown as AuthTokenService;
  const service = new CrmSsoService(
    config,
    authMode,
    { verify } as unknown as CrmSsoTokenService,
    { createLogin } as unknown as CrmSsoRepository,
    { findIdentity } as unknown as ShiwenTeacherReadAdapter,
    tokenService,
    { exchangeCrmSso } as unknown as SessionService,
    { recordSecurityEvent } as unknown as SessionRepository,
  );

  return { service, verify, createLogin, findIdentity, exchangeCrmSso };
}

describe('CrmSsoService', () => {
  it('creates an opaque callback and normalizes unsafe redirects', async () => {
    const fixture = createService();

    await expect(
      fixture.service.start('signed-crm-jwt', 'https://evil.example/path'),
    ).resolves.toBe(
      'https://tide.example.test/sso/callback?code=raw-one-time-exchange-code',
    );
    expect(fixture.createLogin).toHaveBeenCalledWith(
      expect.objectContaining({
        normalizedEmail: 'teacher@51talk.com',
        teacherId: 'TEACHER-001',
        redirectPath: '/',
        jtiHash: 'hash:0123456789abcdef0123456789abcdef',
      }),
    );
  });

  it('does not create an account for a missing source teacher', async () => {
    const fixture = createService();
    fixture.findIdentity.mockResolvedValue(null);

    await expect(
      fixture.service.start('signed-crm-jwt', '/'),
    ).rejects.toBeInstanceOf(UnprocessableEntityException);
    expect(fixture.createLogin).not.toHaveBeenCalled();
  });

  it('maps binding conflicts without automatically rebinding', async () => {
    const fixture = createService();
    fixture.createLogin.mockRejectedValue(
      new CrmSsoBindingError('TEACHER_ALREADY_BOUND'),
    );

    await expect(
      fixture.service.start('signed-crm-jwt', '/'),
    ).rejects.toBeInstanceOf(ConflictException);
  });

  it('exchanges only an opaque one-time code for a TIDE token pair', async () => {
    const fixture = createService();
    fixture.exchangeCrmSso.mockResolvedValue({
      tokenType: 'Bearer',
      accessToken: 'access',
      refreshToken: 'refresh',
      redirectPath: '/path',
    });

    await expect(
      fixture.service.exchange({
        rawCode: 'opaque-code',
        ipAddress: null,
        deviceSummary: null,
      }),
    ).resolves.toMatchObject({ redirectPath: '/path' });
    expect(fixture.exchangeCrmSso).toHaveBeenCalledWith(
      expect.objectContaining({ exchangeCodeHash: 'hash:opaque-code' }),
    );
  });

  it('rejects exchange when the bound account was disabled after entry', async () => {
    const fixture = createService();
    fixture.exchangeCrmSso.mockRejectedValue(
      new CrmSsoSessionAccountInactiveError(),
    );

    try {
      await fixture.service.exchange({
        rawCode: 'opaque-code',
        ipAddress: null,
        deviceSummary: null,
      });
      throw new Error('expected CRM SSO exchange to fail');
    } catch (error) {
      expect(error).toBeInstanceOf(UnauthorizedException);
      expect((error as UnauthorizedException).getResponse()).toMatchObject({
        code: 'ACCOUNT_NOT_ACTIVE',
      });
    }
  });
});
