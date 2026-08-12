import {
  ForbiddenException,
  ServiceUnavailableException,
} from '@nestjs/common';
import type { ConfigService } from '@nestjs/config';
import type { AppEnvironment } from '../platform/config/environment';
import { AuthModeService } from './auth-mode.service';

function service(values: Partial<AppEnvironment>) {
  const config = {
    get: jest.fn((key: keyof AppEnvironment) => values[key]),
  } as unknown as ConfigService<AppEnvironment, true>;
  return new AuthModeService(config);
}

describe('AuthModeService', () => {
  it('keeps existing account routes enabled in hybrid mode', () => {
    const authMode = service({
      TEACHER_AUTH_MODE: 'HYBRID',
      CRM_SSO_JWT_SECRET_CURRENT: 'x'.repeat(32),
    });

    expect(authMode.capabilities()).toMatchObject({
      authMode: 'HYBRID',
      crmSsoEnabled: true,
      passwordLoginEnabled: true,
      registrationEnabled: true,
    });
    expect(() => authMode.assertLocalAuthEnabled()).not.toThrow();
  });

  it('blocks local account routes and requires SSO configuration in SSO-only mode', () => {
    const authMode = service({ TEACHER_AUTH_MODE: 'CRM_SSO_ONLY' });

    expect(() => authMode.assertLocalAuthEnabled()).toThrow(ForbiddenException);
    expect(() => authMode.assertCrmSsoEnabled()).toThrow(
      ServiceUnavailableException,
    );
  });
});
