import {
  ConflictException,
  Injectable,
  ServiceUnavailableException,
  UnauthorizedException,
  UnprocessableEntityException,
} from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { ShiwenTeacherReadAdapter } from '../integrations/shiwen/shiwen-read.adapters';
import {
  ShiwenReadError,
  ShiwenViewNotConfiguredError,
} from '../integrations/shiwen/shiwen-read.errors';
import type { AppEnvironment } from '../platform/config/environment';
import { parseAllowedEmailDomains } from '../platform/config/environment';
import { AuthModeService } from './auth-mode.service';
import { CrmSsoBindingError } from './crm-sso.models';
import { CrmSsoRepository } from './crm-sso.repository';
import { CrmSsoTokenService } from './crm-sso-token.service';
import { AuthTokenService } from './auth-token.service';
import {
  CrmSsoSessionAccountInactiveError,
  SessionRepository,
} from './session.repository';
import { SessionService } from './session.service';

@Injectable()
export class CrmSsoService {
  constructor(
    private readonly config: ConfigService<AppEnvironment, true>,
    private readonly authMode: AuthModeService,
    private readonly verifier: CrmSsoTokenService,
    private readonly repository: CrmSsoRepository,
    private readonly teacherReader: ShiwenTeacherReadAdapter,
    private readonly tokenService: AuthTokenService,
    private readonly sessions: SessionService,
    private readonly sessionRepository: SessionRepository,
  ) {}

  async start(rawToken: string, requestedRedirect?: string): Promise<string> {
    this.authMode.assertCrmSsoEnabled();
    let accountId: string | null = null;

    try {
      const claims = await this.verifier.verify(rawToken);
      const normalizedEmail = claims.email.toLowerCase();
      this.assertAllowedEmailDomain(normalizedEmail);
      const identity = await this.findTeacherIdentity(claims.teacherId);

      if (!identity || identity.dataMode === 'MOCK') {
        throw new UnprocessableEntityException({
          code: 'TEACHER_NOT_FOUND',
          message: '未找到可登录的教师身份',
          retryable: false,
        });
      }

      const now = Date.now();
      const assertionExpiresAt = new Date(claims.exp * 1000);
      const exchangeExpiresAt = new Date(
        Math.min(
          assertionExpiresAt.getTime(),
          now +
            this.config.get('CRM_SSO_EXCHANGE_TTL_SECONDS', { infer: true }) *
              1000,
        ),
      );
      if (exchangeExpiresAt.getTime() <= now + 1_000) {
        throw new UnauthorizedException({
          code: 'CRM_SSO_TOKEN_EXPIRED',
          message: '登录凭证已过期，请从 CRM 重新进入',
          retryable: false,
        });
      }

      const exchangeCode = this.tokenService.create();
      const login = await this.repository.createLogin({
        email: claims.email,
        normalizedEmail,
        teacherId: claims.teacherId,
        jtiHash: this.tokenService.hash(claims.jti),
        issuer: claims.iss,
        audience: claims.aud,
        exchangeCodeHash: exchangeCode.hash,
        redirectPath: this.safeRedirect(requestedRedirect),
        assertionExpiresAt,
        exchangeExpiresAt,
      });
      accountId = login.accountId;

      await this.recordSecurityEvent({
        accountId,
        outcome: 'SUCCESS',
        reasonCode: login.created ? 'ACCOUNT_CREATED' : null,
      });

      const callbackUrl = new URL(
        '/sso/callback',
        this.config.get('PUBLIC_APP_URL', { infer: true }),
      );
      callbackUrl.searchParams.set('code', exchangeCode.raw);
      return callbackUrl.toString();
    } catch (error) {
      const mapped = this.mapBindingError(error);
      await this.recordSecurityEvent({
        accountId,
        outcome: 'BLOCKED',
        reasonCode: this.errorCode(mapped),
      });
      throw mapped;
    }
  }

  async exchange(input: {
    rawCode: string;
    ipAddress: string | null;
    deviceSummary: string | null;
  }) {
    this.authMode.assertCrmSsoEnabled();
    let result;
    try {
      result = await this.sessions.exchangeCrmSso({
        exchangeCodeHash: this.tokenService.hash(input.rawCode),
        ipAddress: input.ipAddress,
        deviceSummary: input.deviceSummary,
      });
    } catch (error) {
      if (error instanceof CrmSsoSessionAccountInactiveError) {
        throw new UnauthorizedException({
          code: 'ACCOUNT_NOT_ACTIVE',
          message: '账号当前不可登录，请联系支持人员',
          retryable: false,
        });
      }
      throw error;
    }

    if (!result) {
      throw new UnauthorizedException({
        code: 'CRM_SSO_EXCHANGE_INVALID',
        message: '登录链接无效或已使用，请从 CRM 重新进入',
        retryable: false,
      });
    }

    return result;
  }

  failureRedirect(error: unknown): string {
    const callbackUrl = new URL(
      '/sso/callback',
      this.config.get('PUBLIC_APP_URL', { infer: true }),
    );
    callbackUrl.searchParams.set('error', this.errorCode(error));
    return callbackUrl.toString();
  }

  private safeRedirect(value?: string): string {
    if (!value) return '/';
    if (value === '/' || value === '/path' || value === '/messages') {
      return value;
    }
    if (/^\/task\/[A-Za-z0-9_-]{1,128}$/.test(value)) return value;
    return '/';
  }

  private assertAllowedEmailDomain(normalizedEmail: string): void {
    const allowedDomains = parseAllowedEmailDomains(
      this.config.get('ALLOWED_EMAIL_DOMAINS', { infer: true }),
    );
    const domain = normalizedEmail.split('@')[1];

    if (
      allowedDomains.length > 0 &&
      (!domain || !allowedDomains.includes(domain))
    ) {
      throw new UnprocessableEntityException({
        code: 'EMAIL_DOMAIN_NOT_ALLOWED',
        message: 'CRM 提供的邮箱不属于允许的公司域名',
        retryable: false,
      });
    }
  }

  private async findTeacherIdentity(teacherId: string) {
    try {
      return await this.teacherReader.findIdentity(teacherId);
    } catch (error) {
      if (
        error instanceof ShiwenViewNotConfiguredError ||
        error instanceof ShiwenReadError
      ) {
        throw new ServiceUnavailableException({
          code: 'SOURCE_UNAVAILABLE',
          message: '教师身份数据暂时不可用，请稍后重试',
          retryable: true,
        });
      }
      throw error;
    }
  }

  private mapBindingError(error: unknown): unknown {
    if (!(error instanceof CrmSsoBindingError)) return error;

    if (error.reason === 'CRM_SSO_REPLAYED') {
      return new UnauthorizedException({
        code: error.reason,
        message: '该登录凭证已使用，请从 CRM 重新进入',
        retryable: false,
      });
    }
    if (error.reason === 'ACCOUNT_NOT_ACTIVE') {
      return new UnauthorizedException({
        code: error.reason,
        message: '账号当前不可登录，请联系支持人员',
        retryable: false,
      });
    }
    return new ConflictException({
      code: error.reason,
      message: '邮箱与教师身份绑定冲突，请联系支持人员处理',
      retryable: false,
    });
  }

  private async recordSecurityEvent(input: {
    accountId: string | null;
    outcome: 'SUCCESS' | 'BLOCKED';
    reasonCode: string | null;
  }): Promise<void> {
    try {
      await this.sessionRepository.recordSecurityEvent({
        accountId: input.accountId,
        eventType: 'CRM_SSO_ASSERTION',
        outcome: input.outcome,
        ipHash: null,
        deviceSummary: null,
        reasonCode: input.reasonCode,
      });
    } catch {
      // 安全事件写入失败不能覆盖原始认证结果。
    }
  }

  private errorCode(error: unknown): string {
    const response =
      typeof error === 'object' && error !== null && 'getResponse' in error
        ? (error as { getResponse: () => unknown }).getResponse()
        : null;
    if (
      typeof response === 'object' &&
      response !== null &&
      'code' in response
    ) {
      return String(response.code).slice(0, 128);
    }
    return 'CRM_SSO_FAILED';
  }
}
