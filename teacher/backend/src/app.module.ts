import { Module, ValidationPipe } from '@nestjs/common';
import { APP_FILTER, APP_INTERCEPTOR, APP_PIPE } from '@nestjs/core';
import { APP_GUARD } from '@nestjs/core';
import { ThrottlerGuard, ThrottlerModule } from '@nestjs/throttler';
import { AuthModule } from './auth/auth.module';
import { FileModule } from './files/file.module';
import { FaqModule } from './faq/faq.module';
import { HealthController } from './health/health.controller';
import { HealthService } from './health/health.service';
import { ShiwenReadModule } from './integrations/shiwen/shiwen-read.module';
import { AiGatewayModule } from './integrations/ai/ai-gateway.module';
import { AppConfigModule } from './platform/config/app-config.module';
import { DatabaseModule } from './platform/database/database.module';
import { ApiExceptionFilter } from './platform/http/api-exception.filter';
import { MultipartUploadConcurrencyInterceptor } from './platform/http/multipart-upload-concurrency.interceptor';
import { LoggingModule } from './platform/observability/logging.module';
import { TideModule } from './tide/tide.module';
import { TaskModule } from './tasks/task.module';
import { AppEventModule } from './app-events/app-event.module';
import { SystemNotificationModule } from './notifications/system-notification.module';
import { SupportTicketModule } from './support-tickets/support-ticket.module';

@Module({
  imports: [
    AppConfigModule,
    LoggingModule,
    DatabaseModule,
    AiGatewayModule,
    ShiwenReadModule,
    AuthModule,
    FaqModule,
    FileModule,
    ThrottlerModule.forRoot([{ ttl: 60_000, limit: 120 }]),
    TideModule,
    TaskModule,
    AppEventModule,
    SystemNotificationModule,
    SupportTicketModule,
  ],
  controllers: [HealthController],
  providers: [
    HealthService,
    {
      provide: APP_FILTER,
      useClass: ApiExceptionFilter,
    },
    {
      provide: APP_PIPE,
      useValue: new ValidationPipe({
        forbidNonWhitelisted: true,
        transform: true,
        whitelist: true,
      }),
    },
    {
      provide: APP_GUARD,
      useClass: ThrottlerGuard,
    },
    {
      provide: APP_INTERCEPTOR,
      useClass: MultipartUploadConcurrencyInterceptor,
    },
  ],
})
export class AppModule {}
