import { Injectable, Optional } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import type { AppEnvironment } from '../../platform/config/environment';
import { DependencyHealthRegistry } from '../../platform/observability/dependency-health.registry';
import {
  MailDeliveryAdapter,
  type PasswordResetEmailMessage,
  type VerificationEmailMessage,
} from './mail-delivery.adapter';

export class CompanyMailDeliveryError extends Error {
  constructor(code: string) {
    super(code);
    this.name = code;
  }
}

@Injectable()
export class CompanyMailDeliveryAdapter extends MailDeliveryAdapter {
  constructor(
    private readonly config: ConfigService<AppEnvironment, true>,
    @Optional() private readonly dependencyHealth?: DependencyHealthRegistry,
  ) {
    super();
  }

  sendVerificationEmail(
    message: VerificationEmailMessage,
  ): Promise<string | null> {
    return this.send(message.email, message.verificationUrl, message);
  }

  sendPasswordResetEmail(
    message: PasswordResetEmailMessage,
  ): Promise<string | null> {
    return this.send(message.email, message.resetUrl, message);
  }

  private async send(
    email: string,
    resetUrl: string,
    message: {
      expiresInMinutes: number;
      templateId: string;
    },
  ): Promise<string | null> {
    const apiUrl = this.config.get('MAIL_API_URL', { infer: true });
    const accessKey = this.config.get('MAIL_API_ACCESS_KEY', { infer: true });
    if (!apiUrl || !accessKey) {
      throw new CompanyMailDeliveryError('MAIL_DELIVERY_NOT_CONFIGURED');
    }

    const startedAt = Date.now();
    let response: Response;
    try {
      response = await fetch(apiUrl, {
        method: 'POST',
        headers: {
          accept: '*/*',
          'content-type': 'application/json',
        },
        body: JSON.stringify({
          access_key: accessKey,
          user_id: this.config.get('MAIL_API_USER_ID', { infer: true }),
          app_name: this.config.get('MAIL_API_APP_NAME', { infer: true }),
          template_id: message.templateId,
          user_type: this.config.get('MAIL_API_USER_TYPE', { infer: true }),
          data: JSON.stringify({
            reset_url: resetUrl,
            expires_in_minutes: String(message.expiresInMinutes),
            email,
          }),
          lang: this.config.get('MAIL_API_LANG', { infer: true }),
        }),
        signal: AbortSignal.timeout(
          this.config.get('MAIL_API_TIMEOUT_MS', { infer: true }),
        ),
      });
    } catch {
      this.recordFailure('MAIL_DELIVERY_UNAVAILABLE', startedAt);
      throw new CompanyMailDeliveryError('MAIL_DELIVERY_UNAVAILABLE');
    }

    if (!response.ok) {
      this.recordFailure(`MAIL_DELIVERY_HTTP_${response.status}`, startedAt);
      throw new CompanyMailDeliveryError('MAIL_DELIVERY_HTTP_ERROR');
    }

    const providerMessageId = await this.readProviderMessageId(response);
    this.dependencyHealth?.recordSuccess('mail', startedAt);
    return providerMessageId;
  }

  private async readProviderMessageId(
    response: Response,
  ): Promise<string | null> {
    const body = await response.text();
    if (!body) return null;

    try {
      const payload = JSON.parse(body) as unknown;
      const root = this.asRecord(payload);
      const data = this.asRecord(root.data ?? root.res);
      return this.stringOrNull(
        data.message_id ??
          data.msg_id ??
          data.id ??
          root.message_id ??
          root.msg_id ??
          root.request_id ??
          root.id,
      );
    } catch {
      return null;
    }
  }

  private recordFailure(code: string, startedAt: number): void {
    this.dependencyHealth?.recordFailure('mail', code, startedAt);
  }

  private asRecord(value: unknown): Record<string, unknown> {
    return value !== null && typeof value === 'object' && !Array.isArray(value)
      ? (value as Record<string, unknown>)
      : {};
  }

  private stringOrNull(value: unknown): string | null {
    if (typeof value === 'string' && value.length > 0) return value;
    if (typeof value === 'number' && Number.isFinite(value)) {
      return String(value);
    }
    return null;
  }
}
