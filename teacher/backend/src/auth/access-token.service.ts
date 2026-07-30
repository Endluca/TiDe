import { Injectable, UnauthorizedException } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { JwtService } from '@nestjs/jwt';
import type { AppEnvironment } from '../platform/config/environment';
import type { AuthPrincipal } from './auth.models';

interface AccessTokenPayload {
  sub: string;
  sid: string;
  typ: 'access';
}

@Injectable()
export class AccessTokenService {
  constructor(
    private readonly jwt: JwtService,
    private readonly config: ConfigService<AppEnvironment, true>,
  ) {}

  issue(principal: AuthPrincipal): Promise<string> {
    return this.jwt.signAsync(
      {
        sub: principal.accountId,
        sid: principal.sessionId,
        typ: 'access',
      },
      {
        expiresIn: this.config.get('ACCESS_TOKEN_TTL_SECONDS', { infer: true }),
      },
    );
  }

  async verify(rawToken: string): Promise<AuthPrincipal> {
    try {
      const payload = await this.jwt.verifyAsync<AccessTokenPayload>(rawToken);

      if (
        payload.typ !== 'access' ||
        typeof payload.sub !== 'string' ||
        typeof payload.sid !== 'string'
      ) {
        throw new Error('invalid access token payload');
      }

      return { accountId: payload.sub, sessionId: payload.sid };
    } catch {
      throw new UnauthorizedException({
        code: 'AUTH_REQUIRED',
        message: '登录状态无效或已过期',
        retryable: false,
      });
    }
  }
}
