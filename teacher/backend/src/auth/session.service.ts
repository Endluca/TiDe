import { Injectable, Optional, UnauthorizedException } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { randomUUID } from 'node:crypto';
import { AppEventService } from '../app-events/app-event.service';
import type { AppEnvironment } from '../platform/config/environment';
import { AccessTokenService } from './access-token.service';
import type { AuthPrincipal, AuthTokenPair } from './auth.models';
import { AuthTokenService } from './auth-token.service';
import { PasswordHasher } from './password-hasher';
import { SessionRepository } from './session.repository';

@Injectable()
export class SessionService {
  constructor(
    private readonly config: ConfigService<AppEnvironment, true>,
    private readonly repository: SessionRepository,
    private readonly passwordHasher: PasswordHasher,
    private readonly tokenService: AuthTokenService,
    private readonly accessTokens: AccessTokenService,
    @Optional() private readonly events?: AppEventService,
  ) {}

  async login(input: {
    email: string;
    password: string;
    ipAddress: string | null;
    deviceSummary: string | null;
    analyticsSessionId?: string;
  }): Promise<AuthTokenPair> {
    const normalizedEmail = input.email.trim().toLowerCase();
    const analyticsSessionId = this.analyticsSessionId(
      input.analyticsSessionId,
    );
    const anonymousActorId =
      this.tokenService.hashPrivateValue(normalizedEmail);
    const account = await this.repository.findLoginAccount(normalizedEmail);
    const passwordMatches = account
      ? await this.passwordHasher.verify(input.password, account.passwordHash)
      : await this.passwordHasher.verifyDummy(input.password);
    const ipHash = input.ipAddress
      ? this.tokenService.hashPrivateValue(input.ipAddress)
      : null;

    if (!account || !passwordMatches) {
      await this.repository.recordSecurityEvent({
        accountId: account?.accountId ?? null,
        eventType: 'LOGIN',
        outcome: 'FAILURE',
        ipHash,
        deviceSummary: input.deviceSummary,
        reasonCode: 'INVALID_CREDENTIALS',
      });
      this.events?.captureSystem({
        eventName: 'ACCOUNT_LOGIN_FAILED',
        accountId: account?.accountId ?? null,
        anonymousActorId,
        sessionId: analyticsSessionId,
        properties: {
          result: 'FAILURE',
          errorCode: 'INVALID_CREDENTIALS',
        },
      });
      throw this.invalidCredentials();
    }

    if (account.status === 'PENDING_VERIFICATION') {
      await this.repository.recordSecurityEvent({
        accountId: account.accountId,
        eventType: 'LOGIN',
        outcome: 'BLOCKED',
        ipHash,
        deviceSummary: input.deviceSummary,
        reasonCode: 'EMAIL_NOT_VERIFIED',
      });
      this.events?.captureSystem({
        eventName: 'ACCOUNT_LOGIN_FAILED',
        accountId: account.accountId,
        anonymousActorId,
        sessionId: analyticsSessionId,
        properties: {
          result: 'BLOCKED',
          errorCode: 'EMAIL_NOT_VERIFIED',
        },
      });
      throw new UnauthorizedException({
        code: 'EMAIL_NOT_VERIFIED',
        message: '请先完成邮箱验证',
        retryable: false,
      });
    }

    if (account.status !== 'ACTIVE') {
      this.events?.captureSystem({
        eventName: 'ACCOUNT_LOGIN_FAILED',
        accountId: account.accountId,
        anonymousActorId,
        sessionId: analyticsSessionId,
        properties: {
          result: 'BLOCKED',
          errorCode: 'ACCOUNT_NOT_ACTIVE',
        },
      });
      throw this.invalidCredentials();
    }

    const tokenPair = await this.createSessionTokenPair({
      accountId: account.accountId,
      ipHash,
      deviceSummary: input.deviceSummary,
    });
    await this.repository.recordSecurityEvent({
      accountId: account.accountId,
      eventType: 'LOGIN',
      outcome: 'SUCCESS',
      ipHash,
      deviceSummary: input.deviceSummary,
      reasonCode: null,
    });
    this.events?.captureSystem({
      eventName: 'ACCOUNT_LOGIN_SUCCEEDED',
      accountId: account.accountId,
      sessionId: analyticsSessionId,
      properties: { result: 'SUCCESS' },
    });
    return tokenPair;
  }

  async refresh(input: {
    refreshToken: string;
    ipAddress: string | null;
    deviceSummary: string | null;
  }): Promise<AuthTokenPair> {
    const previousHash = this.tokenService.hash(input.refreshToken);
    const session = await this.repository.findByRefreshTokenHash(previousHash);

    if (!session || session.accountStatus !== 'ACTIVE') {
      throw this.invalidCredentials();
    }

    const nextToken = this.tokenService.create();
    const rotated = await this.repository.rotateRefreshToken({
      sessionId: session.sessionId,
      previousHash,
      nextHash: nextToken.hash,
      deviceSummary: input.deviceSummary,
      ipHash: input.ipAddress
        ? this.tokenService.hashPrivateValue(input.ipAddress)
        : null,
    });

    if (!rotated) {
      throw this.invalidCredentials();
    }

    const accessToken = await this.accessTokens.issue({
      accountId: session.accountId,
      sessionId: session.sessionId,
    });

    return {
      tokenType: 'Bearer',
      accessToken,
      accessTokenExpiresIn: this.config.get('ACCESS_TOKEN_TTL_SECONDS', {
        infer: true,
      }),
      refreshToken: nextToken.raw,
      refreshTokenExpiresAt: session.expiresAt.toISOString(),
    };
  }

  async logout(principal: AuthPrincipal): Promise<void> {
    await this.repository.revoke(principal.sessionId, principal.accountId);
  }

  private async createSessionTokenPair(input: {
    accountId: string;
    ipHash: string | null;
    deviceSummary: string | null;
  }): Promise<AuthTokenPair> {
    const sessionId = randomUUID();
    const refreshToken = this.tokenService.create();
    const refreshTokenExpiresAt = new Date(
      Date.now() +
        this.config.get('REFRESH_TOKEN_TTL_DAYS', { infer: true }) *
          24 *
          60 *
          60 *
          1000,
    );

    await this.repository.createSession({
      sessionId,
      accountId: input.accountId,
      refreshTokenHash: refreshToken.hash,
      deviceSummary: input.deviceSummary,
      ipHash: input.ipHash,
      expiresAt: refreshTokenExpiresAt,
    });
    const accessToken = await this.accessTokens.issue({
      accountId: input.accountId,
      sessionId,
    });

    return {
      tokenType: 'Bearer',
      accessToken,
      accessTokenExpiresIn: this.config.get('ACCESS_TOKEN_TTL_SECONDS', {
        infer: true,
      }),
      refreshToken: refreshToken.raw,
      refreshTokenExpiresAt: refreshTokenExpiresAt.toISOString(),
    };
  }

  private invalidCredentials(): UnauthorizedException {
    return new UnauthorizedException({
      code: 'INVALID_CREDENTIALS',
      message: '邮箱或密码错误',
      retryable: false,
    });
  }

  private analyticsSessionId(value?: string): string {
    return value && value.length >= 8 && value.length <= 128
      ? value
      : `backend-auth-${randomUUID()}`;
  }
}
