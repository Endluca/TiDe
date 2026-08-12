import { Injectable, UnauthorizedException } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { JwtService } from '@nestjs/jwt';
import { isEmail } from 'class-validator';
import type { AppEnvironment } from '../platform/config/environment';
import type { CrmSsoClaims } from './crm-sso.models';

@Injectable()
export class CrmSsoTokenService {
  constructor(
    private readonly config: ConfigService<AppEnvironment, true>,
    private readonly jwt: JwtService,
  ) {}

  async verify(rawToken: string): Promise<CrmSsoClaims> {
    const secrets = [
      this.config.get('CRM_SSO_JWT_SECRET_CURRENT', { infer: true }),
      this.config.get('CRM_SSO_JWT_SECRET_PREVIOUS', { infer: true }),
    ].filter((secret): secret is string => Boolean(secret));
    let lastError: unknown;
    let expired = false;

    for (const secret of secrets) {
      try {
        const payload = await this.jwt.verifyAsync<Record<string, unknown>>(
          rawToken,
          {
            secret,
            algorithms: ['HS256'],
            issuer: this.config.get('CRM_SSO_ISSUER', { infer: true }),
            audience: this.config.get('CRM_SSO_AUDIENCE', { infer: true }),
            clockTolerance: this.config.get('CRM_SSO_CLOCK_TOLERANCE_SECONDS', {
              infer: true,
            }),
          },
        );
        return this.parseClaims(payload);
      } catch (error) {
        lastError = error;
        expired ||=
          error instanceof Error && error.name === 'TokenExpiredError';
      }
    }

    throw this.invalidToken(lastError, expired);
  }

  private parseClaims(payload: Record<string, unknown>): CrmSsoClaims {
    const iat = payload.iat;
    const exp = payload.exp;
    const jti = payload.jti;
    const teacherId = payload.teacher_id;
    const email = payload.email;
    const issuer = payload.iss;
    const audience = payload.aud;
    const now = Math.floor(Date.now() / 1000);
    const maxTtl = this.config.get('CRM_SSO_MAX_TTL_SECONDS', { infer: true });
    const tolerance = this.config.get('CRM_SSO_CLOCK_TOLERANCE_SECONDS', {
      infer: true,
    });

    if (
      !Number.isInteger(iat) ||
      !Number.isInteger(exp) ||
      typeof iat !== 'number' ||
      typeof exp !== 'number' ||
      exp <= iat ||
      exp - iat > maxTtl ||
      iat > now + tolerance ||
      typeof jti !== 'string' ||
      jti.length < 16 ||
      jti.length > 256 ||
      typeof teacherId !== 'string' ||
      teacherId.trim().length < 1 ||
      teacherId.trim().length > 128 ||
      typeof email !== 'string' ||
      email.length > 320 ||
      !isEmail(email.trim()) ||
      typeof issuer !== 'string' ||
      typeof audience !== 'string'
    ) {
      throw this.invalidToken();
    }

    return {
      iat,
      exp,
      jti,
      iss: issuer,
      aud: audience,
      teacherId: teacherId.trim(),
      email: email.trim(),
    };
  }

  private invalidToken(
    error?: unknown,
    expired = error instanceof Error && error.name === 'TokenExpiredError',
  ): UnauthorizedException {
    return new UnauthorizedException({
      code: expired ? 'CRM_SSO_TOKEN_EXPIRED' : 'CRM_SSO_TOKEN_INVALID',
      message: expired
        ? '登录凭证已过期，请从 CRM 重新进入'
        : '登录凭证无效，请从 CRM 重新进入',
      retryable: false,
    });
  }
}
