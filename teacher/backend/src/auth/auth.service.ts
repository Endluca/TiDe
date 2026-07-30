import {
  ConflictException,
  Injectable,
  Optional,
  ServiceUnavailableException,
  UnprocessableEntityException,
} from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { randomUUID } from 'node:crypto';
import { AppEventService } from '../app-events/app-event.service';
import { MailDeliveryAdapter } from '../integrations/mail/mail-delivery.adapter';
import { ShiwenTeacherReadAdapter } from '../integrations/shiwen/shiwen-read.adapters';
import {
  ShiwenReadError,
  ShiwenViewNotConfiguredError,
} from '../integrations/shiwen/shiwen-read.errors';
import type { AppEnvironment } from '../platform/config/environment';
import { parseAllowedEmailDomains } from '../platform/config/environment';
import { AuthRepository, RegistrationConflictError } from './auth.repository';
import { AuthTokenService } from './auth-token.service';
import type {
  EmailVerificationResult,
  RegistrationAccepted,
  VerificationResendAccepted,
} from './auth.models';
import { PasswordHasher } from './password-hasher';

@Injectable()
export class AuthService {
  constructor(
    private readonly config: ConfigService<AppEnvironment, true>,
    private readonly repository: AuthRepository,
    private readonly teacherReader: ShiwenTeacherReadAdapter,
    private readonly mail: MailDeliveryAdapter,
    private readonly passwordHasher: PasswordHasher,
    private readonly tokenService: AuthTokenService,
    @Optional() private readonly events?: AppEventService,
  ) {}

  async register(
    input: {
      email: string;
      teacherId: string;
      password: string;
    },
    analyticsSessionId?: string,
  ): Promise<RegistrationAccepted> {
    const email = input.email.trim();
    const normalizedEmail = email.toLowerCase();
    const teacherId = input.teacherId.trim();
    const sessionId = this.analyticsSessionId(analyticsSessionId);
    const anonymousActorId =
      this.tokenService.hashPrivateValue(normalizedEmail);
    this.events?.captureSystem({
      eventName: 'ACCOUNT_REGISTER_STARTED',
      anonymousActorId,
      sessionId,
      properties: { result: 'STARTED' },
    });

    try {
      this.assertAllowedEmailDomain(normalizedEmail);

      const identity = await this.findTeacherIdentity(teacherId);

      if (!identity || identity.dataMode === 'MOCK') {
        throw new UnprocessableEntityException({
          code: 'TEACHER_NOT_FOUND',
          message: '未找到可用于注册的教师编号',
          retryable: false,
        });
      }

      const passwordHash = await this.passwordHasher.hash(input.password);
      const token = this.tokenService.create();
      const accountId = randomUUID();
      const deliveryId = randomUUID();
      const expiresInMinutes = this.config.get(
        'EMAIL_VERIFICATION_TTL_MINUTES',
        {
          infer: true,
        },
      );
      const tokenExpiresAt = new Date(
        Date.now() + expiresInMinutes * 60 * 1000,
      );

      try {
        await this.repository.createRegistration({
          accountId,
          bindingId: randomUUID(),
          bindingAuditId: randomUUID(),
          tokenId: randomUUID(),
          deliveryId,
          email,
          normalizedEmail,
          passwordHash,
          teacherId,
          tokenHash: token.hash,
          tokenExpiresAt,
          recipientEmailHash:
            this.tokenService.hashPrivateValue(normalizedEmail),
          templateId: this.config.get('MAIL_VERIFY_TEMPLATE_ID', {
            infer: true,
          }),
        });
      } catch (error) {
        if (error instanceof RegistrationConflictError) {
          throw new ConflictException({
            code: error.reason,
            message:
              error.reason === 'EMAIL_ALREADY_BOUND'
                ? '该邮箱已绑定账号'
                : '该教师编号已绑定账号',
            retryable: false,
          });
        }

        throw error;
      }

      const verificationEmailSent = await this.sendVerificationEmail({
        accountId,
        deliveryId,
        email,
        rawToken: token.raw,
        expiresInMinutes,
        analyticsSessionId: sessionId,
      });
      this.events?.captureSystem({
        eventName: 'ACCOUNT_REGISTER_SUCCEEDED',
        accountId,
        sessionId,
        properties: { result: 'SUCCESS' },
      });

      return { status: 'VERIFICATION_REQUIRED', verificationEmailSent };
    } catch (error) {
      this.events?.captureSystem({
        eventName: 'ACCOUNT_REGISTER_FAILED',
        anonymousActorId,
        sessionId,
        properties: {
          result: 'FAILURE',
          errorCode: this.errorCode(error),
        },
      });
      throw error;
    }
  }

  async confirmEmail(
    rawToken: string,
    analyticsSessionId?: string,
  ): Promise<EmailVerificationResult> {
    const sessionId = this.analyticsSessionId(analyticsSessionId);
    const anonymousActorId = this.tokenService.hashPrivateValue(rawToken);
    const result = await this.repository.consumeVerificationToken(
      this.tokenService.hash(rawToken),
    );

    if (result === 'INVALID_OR_EXPIRED') {
      throw new UnprocessableEntityException({
        code: 'VERIFICATION_TOKEN_INVALID',
        message: '验证链接无效或已过期，请重新发送',
        retryable: false,
      });
    }
    this.events?.captureSystem({
      eventName: 'ACCOUNT_VERIFICATION_SUCCEEDED',
      anonymousActorId,
      sessionId,
      properties: { result },
    });

    return { status: result };
  }

  async resendVerification(
    emailInput: string,
    analyticsSessionId?: string,
  ): Promise<VerificationResendAccepted> {
    const normalizedEmail = emailInput.trim().toLowerCase();
    const account =
      await this.repository.findPendingAccountByEmail(normalizedEmail);

    if (!account || account.accountStatus !== 'PENDING_VERIFICATION') {
      return { accepted: true };
    }

    const token = this.tokenService.create();
    const deliveryId = randomUUID();
    const expiresInMinutes = this.config.get('EMAIL_VERIFICATION_TTL_MINUTES', {
      infer: true,
    });

    await this.repository.createVerificationChallenge({
      accountId: account.accountId,
      tokenId: randomUUID(),
      deliveryId,
      tokenHash: token.hash,
      tokenExpiresAt: new Date(Date.now() + expiresInMinutes * 60 * 1000),
      recipientEmailHash: this.tokenService.hashPrivateValue(normalizedEmail),
      templateId: this.config.get('MAIL_VERIFY_TEMPLATE_ID', { infer: true }),
    });
    await this.sendVerificationEmail({
      accountId: account.accountId,
      deliveryId,
      email: account.email,
      rawToken: token.raw,
      expiresInMinutes,
      analyticsSessionId: this.analyticsSessionId(analyticsSessionId),
    });

    return { accepted: true };
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

  private assertAllowedEmailDomain(normalizedEmail: string): void {
    const allowedDomains = parseAllowedEmailDomains(
      this.config.get('ALLOWED_EMAIL_DOMAINS', { infer: true }),
    );

    if (allowedDomains.length === 0) {
      return;
    }

    const domain = normalizedEmail.split('@')[1];

    if (!domain || !allowedDomains.includes(domain)) {
      throw new UnprocessableEntityException({
        code: 'EMAIL_DOMAIN_NOT_ALLOWED',
        message: '请使用公司邮箱注册',
        retryable: false,
      });
    }
  }

  private async sendVerificationEmail(input: {
    accountId: string;
    deliveryId: string;
    email: string;
    rawToken: string;
    expiresInMinutes: number;
    analyticsSessionId: string;
  }): Promise<boolean> {
    const publicAppUrl = this.config.get('PUBLIC_APP_URL', { infer: true });
    const verificationUrl = new URL('/verify-email', publicAppUrl);
    verificationUrl.searchParams.set('token', input.rawToken);

    let providerMessageId: string | null;

    try {
      providerMessageId = await this.mail.sendVerificationEmail({
        email: input.email,
        verificationUrl: verificationUrl.toString(),
        expiresInMinutes: input.expiresInMinutes,
        templateId: this.config.get('MAIL_VERIFY_TEMPLATE_ID', { infer: true }),
      });
    } catch (error) {
      await this.repository.markEmailDelivery(
        input.deliveryId,
        'FAILED',
        null,
        error instanceof Error ? error.name : 'UNKNOWN_MAIL_ERROR',
      );
      this.events?.captureSystem({
        eventName: 'ACCOUNT_VERIFICATION_EMAIL_SENT',
        accountId: input.accountId,
        sessionId: input.analyticsSessionId,
        properties: {
          result: 'FAILURE',
          errorCode: error instanceof Error ? error.name : 'UNKNOWN_MAIL_ERROR',
        },
      });
      return false;
    }

    await this.repository.markEmailDelivery(
      input.deliveryId,
      'SENT',
      providerMessageId,
      null,
    );
    this.events?.captureSystem({
      eventName: 'ACCOUNT_VERIFICATION_EMAIL_SENT',
      accountId: input.accountId,
      sessionId: input.analyticsSessionId,
      properties: { result: 'SUCCESS' },
    });
    return true;
  }

  private analyticsSessionId(value?: string): string {
    return value && value.length >= 8 && value.length <= 128
      ? value
      : `backend-auth-${randomUUID()}`;
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
    return error instanceof Error ? error.name : 'UNKNOWN_ERROR';
  }
}
