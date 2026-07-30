import { Injectable, Optional } from '@nestjs/common';
import { DependencyHealthRegistry } from '../../platform/observability/dependency-health.registry';
import { MailDeliveryAdapter } from './mail-delivery.adapter';

export class MailDeliveryUnavailableError extends Error {
  constructor() {
    super('公司邮件服务尚未完成配置');
    this.name = 'MailDeliveryUnavailableError';
  }
}

@Injectable()
export class UnavailableMailDeliveryAdapter extends MailDeliveryAdapter {
  constructor(
    @Optional() private readonly dependencyHealth?: DependencyHealthRegistry,
  ) {
    super();
  }

  sendVerificationEmail(): Promise<string | null> {
    this.recordUnavailable();
    return Promise.reject(new MailDeliveryUnavailableError());
  }

  sendPasswordResetEmail(): Promise<string | null> {
    this.recordUnavailable();
    return Promise.reject(new MailDeliveryUnavailableError());
  }

  private recordUnavailable(): void {
    const startedAt = Date.now();
    this.dependencyHealth?.recordFailure(
      'mail',
      'MAIL_DELIVERY_NOT_CONFIGURED',
      startedAt,
    );
  }
}
