import { UnauthorizedException } from '@nestjs/common';
import type { ConfigService } from '@nestjs/config';
import { JwtService } from '@nestjs/jwt';
import type { AppEnvironment } from '../platform/config/environment';
import { CrmSsoTokenService } from './crm-sso-token.service';

const currentSecret = 'current-crm-sso-secret-with-at-least-32-characters';
const previousSecret = 'previous-crm-sso-secret-with-at-least-32-characters';

function createVerifier() {
  const values: Partial<AppEnvironment> = {
    CRM_SSO_JWT_SECRET_CURRENT: currentSecret,
    CRM_SSO_JWT_SECRET_PREVIOUS: previousSecret,
    CRM_SSO_ISSUER: 'crm',
    CRM_SSO_AUDIENCE: 'tide',
    CRM_SSO_MAX_TTL_SECONDS: 120,
    CRM_SSO_CLOCK_TOLERANCE_SECONDS: 30,
  };
  const config = {
    get: jest.fn((key: keyof AppEnvironment) => values[key]),
  } as unknown as ConfigService<AppEnvironment, true>;
  const jwt = new JwtService();
  return { service: new CrmSsoTokenService(config, jwt), jwt };
}

function payload(overrides: Record<string, unknown> = {}) {
  const now = Math.floor(Date.now() / 1000);
  return {
    iat: now,
    exp: now + 120,
    jti: '0123456789abcdef0123456789abcdef',
    iss: 'crm',
    aud: 'tide',
    teacher_id: 'TEACHER-001',
    email: 'teacher@51talk.com',
    ...overrides,
  };
}

describe('CrmSsoTokenService', () => {
  it('accepts current and previous HS256 secrets', async () => {
    const fixture = createVerifier();
    const current = await fixture.jwt.signAsync(payload(), {
      secret: currentSecret,
      algorithm: 'HS256',
    });
    const previous = await fixture.jwt.signAsync(
      payload({
        jti: 'abcdef0123456789abcdef0123456789',
      }),
      {
        secret: previousSecret,
        algorithm: 'HS256',
      },
    );

    await expect(fixture.service.verify(current)).resolves.toMatchObject({
      teacherId: 'TEACHER-001',
      email: 'teacher@51talk.com',
    });
    await expect(fixture.service.verify(previous)).resolves.toMatchObject({
      jti: 'abcdef0123456789abcdef0123456789',
    });
  });

  it('rejects assertions that exceed the agreed lifetime', async () => {
    const fixture = createVerifier();
    const now = Math.floor(Date.now() / 1000);
    const token = await fixture.jwt.signAsync(
      payload({ iat: now, exp: now + 121 }),
      { secret: currentSecret, algorithm: 'HS256' },
    );

    await expect(fixture.service.verify(token)).rejects.toBeInstanceOf(
      UnauthorizedException,
    );
  });

  it('rejects expired, incorrectly addressed, or incomplete assertions', async () => {
    const fixture = createVerifier();
    const now = Math.floor(Date.now() / 1000);
    const invalidPayloads = [
      payload({ iat: now - 100, exp: now - 40 }),
      payload({ aud: 'another-app' }),
      payload({ teacher_id: '' }),
    ];

    for (const invalid of invalidPayloads) {
      const token = await fixture.jwt.signAsync(invalid, {
        secret: currentSecret,
        algorithm: 'HS256',
      });
      await expect(fixture.service.verify(token)).rejects.toBeInstanceOf(
        UnauthorizedException,
      );
    }
  });

  it('keeps the expired error when the current-secret token is checked against both rotation keys', async () => {
    const fixture = createVerifier();
    const now = Math.floor(Date.now() / 1000);
    const token = await fixture.jwt.signAsync(
      payload({ iat: now - 100, exp: now - 40 }),
      { secret: currentSecret, algorithm: 'HS256' },
    );

    try {
      await fixture.service.verify(token);
      throw new Error('expected CRM SSO token verification to fail');
    } catch (error) {
      expect(error).toBeInstanceOf(UnauthorizedException);
      expect((error as UnauthorizedException).getResponse()).toMatchObject({
        code: 'CRM_SSO_TOKEN_EXPIRED',
      });
    }
  });
});
