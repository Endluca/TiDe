import type { ConfigService } from '@nestjs/config';
import { UnauthorizedException } from '@nestjs/common';
import type { AppEnvironment } from '../platform/config/environment';
import type { AccessTokenService } from './access-token.service';
import type { AuthTokenService } from './auth-token.service';
import type { PasswordHasher } from './password-hasher';
import type { SessionRepository } from './session.repository';
import { SessionService } from './session.service';

function createSessionService() {
  const values: Partial<AppEnvironment> = {
    ACCESS_TOKEN_TTL_SECONDS: 900,
    REFRESH_TOKEN_TTL_DAYS: 7,
  };
  const config = {
    get: jest.fn((key: keyof AppEnvironment) => values[key]),
  } as unknown as ConfigService<AppEnvironment, true>;
  const findLoginAccount = jest.fn().mockResolvedValue({
    accountId: 'account-001',
    passwordHash: 'stored-password-hash',
    status: 'ACTIVE',
  });
  const createSession = jest.fn().mockResolvedValue(undefined);
  const recordSecurityEvent = jest.fn().mockResolvedValue(undefined);
  const repository = {
    findLoginAccount,
    createSession,
    recordSecurityEvent,
  } as unknown as SessionRepository;
  const verify = jest.fn().mockResolvedValue(true);
  const verifyDummy = jest.fn().mockResolvedValue(false);
  const passwordHasher = { verify, verifyDummy } as unknown as PasswordHasher;
  const tokenService = {
    create: jest
      .fn()
      .mockReturnValue({ raw: 'raw-refresh', hash: 'hash-refresh' }),
    hashPrivateValue: jest.fn().mockReturnValue('private-ip-hash'),
  } as unknown as AuthTokenService;
  const issue = jest.fn().mockResolvedValue('signed-access-token');
  const accessTokens = { issue } as unknown as AccessTokenService;

  return {
    service: new SessionService(
      config,
      repository,
      passwordHasher,
      tokenService,
      accessTokens,
    ),
    findLoginAccount,
    createSession,
    recordSecurityEvent,
    verify,
    verifyDummy,
    issue,
  };
}

describe('SessionService', () => {
  it('creates a hashed refresh session and a short-lived access token', async () => {
    const fixture = createSessionService();

    const result = await fixture.service.login({
      email: ' Teacher@51Talk.com ',
      password: 'correct-password',
      ipAddress: '127.0.0.1',
      deviceSummary: 'test-agent',
    });

    expect(result).toMatchObject({
      tokenType: 'Bearer',
      accessToken: 'signed-access-token',
      accessTokenExpiresIn: 900,
      refreshToken: 'raw-refresh',
    });
    expect(fixture.findLoginAccount).toHaveBeenCalledWith('teacher@51talk.com');
    expect(fixture.createSession).toHaveBeenCalledWith(
      expect.objectContaining({
        accountId: 'account-001',
        refreshTokenHash: 'hash-refresh',
        ipHash: 'private-ip-hash',
      }),
    );
    expect(JSON.stringify(fixture.createSession.mock.calls)).not.toContain(
      'raw-refresh',
    );
    expect(fixture.issue).toHaveBeenCalledTimes(1);
    expect(fixture.recordSecurityEvent).toHaveBeenCalledWith(
      expect.objectContaining({ outcome: 'SUCCESS' }),
    );
  });

  it('runs the same expensive password path for an unknown account', async () => {
    const fixture = createSessionService();
    fixture.findLoginAccount.mockResolvedValue(null);

    await expect(
      fixture.service.login({
        email: 'unknown@51talk.com',
        password: 'wrong-password',
        ipAddress: null,
        deviceSummary: null,
      }),
    ).rejects.toBeInstanceOf(UnauthorizedException);
    expect(fixture.verifyDummy).toHaveBeenCalledWith('wrong-password');
    expect(fixture.verify).not.toHaveBeenCalled();
  });
});
