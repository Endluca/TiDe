import {
  Injectable,
  Optional,
  UnprocessableEntityException,
} from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { randomUUID } from 'node:crypto';
import { AppEventService } from '../app-events/app-event.service';
import { MailDeliveryAdapter } from '../integrations/mail/mail-delivery.adapter';
import type { AppEnvironment } from '../platform/config/environment';
import type {
  PasswordResetAccepted,
  PasswordResetCompleted,
} from './auth.models';
import { AuthTokenService } from './auth-token.service';
import { PasswordHasher } from './password-hasher';
import { PasswordResetRepository } from './password-reset.repository';

@Injectable()
export class PasswordResetService {
  constructor(
    private readonly config: ConfigService<AppEnvironment, true>,
    private readonly repository: PasswordResetRepository,
    private readonly mail: MailDeliveryAdapter,
    private readonly passwordHasher: PasswordHasher,
    private readonly tokenService: AuthTokenService,
    @Optional() private readonly events?: AppEventService,
  ) {}

  async request(
    emailInput: string,
    analyticsSessionId?: string,
  ): Promise<PasswordResetAccepted> {
    const normalizedEmail = emailInput.trim().toLowerCase();
    const sessionId = this.analyticsSessionId(analyticsSessionId);
    const anonymousActorId =
      this.tokenService.hashPrivateValue(normalizedEmail);
    const account = await this.repository.findAccount(normalizedEmail);
    this.events?.captureSystem({
      eventName: 'ACCOUNT_PASSWORD_RESET_STARTED',
      accountId: account?.accountId ?? null,
      anonymousActorId,
      sessionId,
      properties: { result: 'STARTED' },
    });

    if (!account || account.status !== 'ACTIVE') {
      return { accepted: true };
    }

    const token = this.tokenService.create();
    const deliveryId = randomUUID();
    const expiresInMinutes = this.config.get('PASSWORD_RESET_TTL_MINUTES', {
      infer: true,
    });
    await this.repository.createChallenge({
      accountId: account.accountId,
      tokenId: randomUUID(),
      deliveryId,
      tokenHash: token.hash,
      tokenExpiresAt: new Date(Date.now() + expiresInMinutes * 60 * 1000),
      recipientEmailHash: this.tokenService.hashPrivateValue(normalizedEmail),
      templateId: this.config.get('MAIL_PASSWORD_RESET_TEMPLATE_ID', {
        infer: true,
      }),
    });
    const sent = await this.sendResetEmail({
      deliveryId,
      email: account.email,
      rawToken: token.raw,
      expiresInMinutes,
    });
    if (!sent) {
      this.events?.captureSystem({
        eventName: 'ACCOUNT_PASSWORD_RESET_FAILED',
        accountId: account.accountId,
        sessionId,
        properties: {
          result: 'FAILURE',
          errorCode: 'PASSWORD_RESET_EMAIL_FAILED',
        },
      });
    }

    return { accepted: true };
  }

  async confirm(
    rawToken: string,
    newPassword: string,
    analyticsSessionId?: string,
  ): Promise<PasswordResetCompleted> {
    const sessionId = this.analyticsSessionId(analyticsSessionId);
    const anonymousActorId = this.tokenService.hashPrivateValue(rawToken);
    const nextPasswordHash = await this.passwordHasher.hash(newPassword);
    const consumed = await this.repository.consumeToken(
      this.tokenService.hash(rawToken),
      nextPasswordHash,
    );

    if (!consumed) {
      this.events?.captureSystem({
        eventName: 'ACCOUNT_PASSWORD_RESET_FAILED',
        anonymousActorId,
        sessionId,
        properties: {
          result: 'FAILURE',
          errorCode: 'PASSWORD_RESET_TOKEN_INVALID',
        },
      });
      throw new UnprocessableEntityException({
        code: 'PASSWORD_RESET_TOKEN_INVALID',
        message: '重置链接无效或已过期，请重新申请',
        retryable: false,
      });
    }
    this.events?.captureSystem({
      eventName: 'ACCOUNT_PASSWORD_RESET_SUCCEEDED',
      anonymousActorId,
      sessionId,
      properties: { result: 'SUCCESS' },
    });

    return { status: 'PASSWORD_UPDATED' };
  }

  private async sendResetEmail(input: {
    deliveryId: string;
    email: string;
    rawToken: string;
    expiresInMinutes: number;
  }): Promise<boolean> {
    const resetUrl = new URL(
      '/reset-password',
      this.config.get('PUBLIC_APP_URL', { infer: true }),
    );
    resetUrl.searchParams.set('token', input.rawToken);
    let providerMessageId: string | null;

    try {
      providerMessageId = await this.mail.sendPasswordResetEmail({
        email: input.email,
        resetUrl: resetUrl.toString(),
        expiresInMinutes: input.expiresInMinutes,
        templateId: this.config.get('MAIL_PASSWORD_RESET_TEMPLATE_ID', {
          infer: true,
        }),
      });
    } catch (error) {
      await this.repository.markDelivery(
        input.deliveryId,
        'FAILED',
        null,
        error instanceof Error ? error.name : 'UNKNOWN_MAIL_ERROR',
      );
      return false;
    }

    await this.repository.markDelivery(
      input.deliveryId,
      'SENT',
      providerMessageId,
      null,
    );
    return true;
  }

  private analyticsSessionId(value?: string): string {
    return value && value.length >= 8 && value.length <= 128
      ? value
      : `backend-auth-${randomUUID()}`;
  }
}
