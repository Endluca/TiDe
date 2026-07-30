import { Module } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import type { AppEnvironment } from '../../platform/config/environment';
import { CompanyMailDeliveryAdapter } from './company-mail-delivery.adapter';
import { MailDeliveryAdapter } from './mail-delivery.adapter';
import { UnavailableMailDeliveryAdapter } from './unavailable-mail-delivery.adapter';

@Module({
  providers: [
    CompanyMailDeliveryAdapter,
    UnavailableMailDeliveryAdapter,
    {
      provide: MailDeliveryAdapter,
      inject: [
        ConfigService,
        CompanyMailDeliveryAdapter,
        UnavailableMailDeliveryAdapter,
      ],
      useFactory: (
        config: ConfigService<AppEnvironment, true>,
        companyMail: CompanyMailDeliveryAdapter,
        unavailableMail: UnavailableMailDeliveryAdapter,
      ) =>
        config.get('MAIL_DELIVERY_PROVIDER', { infer: true }) ===
        'COMPANY_MESSAGE_API'
          ? companyMail
          : unavailableMail,
    },
  ],
  exports: [MailDeliveryAdapter],
})
export class MailModule {}
