import { Module } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { JwtModule } from '@nestjs/jwt';
import type { AppEnvironment } from '../platform/config/environment';
import { MailModule } from '../integrations/mail/mail.module';
import { ShiwenReadModule } from '../integrations/shiwen/shiwen-read.module';
import { AppEventStoreModule } from '../app-events/app-event-store.module';
import { AuthController } from './auth.controller';
import { AuthRepository } from './auth.repository';
import { AuthService } from './auth.service';
import { AuthTokenService } from './auth-token.service';
import { AccessTokenService } from './access-token.service';
import { PasswordResetRepository } from './password-reset.repository';
import { PasswordResetService } from './password-reset.service';
import { PasswordHasher } from './password-hasher';
import { SessionAuthGuard } from './session-auth.guard';
import { SessionRepository } from './session.repository';
import { SessionService } from './session.service';

@Module({
  imports: [
    ShiwenReadModule,
    MailModule,
    AppEventStoreModule,
    JwtModule.registerAsync({
      inject: [ConfigService],
      useFactory: (config: ConfigService<AppEnvironment, true>) => ({
        secret:
          config.get('AUTH_JWT_SECRET', { infer: true }) ??
          'development-only-jwt-secret-change-before-production',
        signOptions: {
          issuer: 'tide-backend',
          audience: 'tide-teacher',
        },
        verifyOptions: {
          issuer: 'tide-backend',
          audience: 'tide-teacher',
        },
      }),
    }),
  ],
  controllers: [AuthController],
  providers: [
    AuthService,
    AuthRepository,
    AuthTokenService,
    PasswordHasher,
    AccessTokenService,
    SessionRepository,
    SessionService,
    SessionAuthGuard,
    PasswordResetRepository,
    PasswordResetService,
  ],
  exports: [
    AuthService,
    AccessTokenService,
    SessionAuthGuard,
    SessionRepository,
  ],
})
export class AuthModule {}
