import {
  CanActivate,
  ExecutionContext,
  Injectable,
  UnauthorizedException,
} from '@nestjs/common';
import type { Request } from 'express';
import { ConfigService } from '@nestjs/config';
import type { AppEnvironment } from '../platform/config/environment';
import { AccessTokenService } from './access-token.service';
import type { AuthPrincipal } from './auth.models';
import { SessionRepository } from './session.repository';

export interface AuthenticatedRequest extends Request {
  auth: AuthPrincipal;
}

@Injectable()
export class SessionAuthGuard implements CanActivate {
  constructor(
    private readonly accessTokens: AccessTokenService,
    private readonly sessions: SessionRepository,
    private readonly config: ConfigService<AppEnvironment, true>,
  ) {}

  async canActivate(context: ExecutionContext): Promise<boolean> {
    const request = context.switchToHttp().getRequest<AuthenticatedRequest>();
    const authorization = request.headers.authorization;

    if (!authorization?.startsWith('Bearer ')) {
      throw this.unauthorized();
    }

    const principal = await this.accessTokens.verify(authorization.slice(7));
    const active = await this.sessions.isActive(
      principal.sessionId,
      principal.accountId,
      this.config.get('TEACHER_AUTH_MODE', { infer: true }) === 'CRM_SSO_ONLY'
        ? 'CRM_SSO'
        : null,
    );

    if (!active) {
      throw this.unauthorized();
    }

    request.auth = principal;
    return true;
  }

  private unauthorized(): UnauthorizedException {
    return new UnauthorizedException({
      code: 'AUTH_REQUIRED',
      message: '登录状态无效或已过期',
      retryable: false,
    });
  }
}
