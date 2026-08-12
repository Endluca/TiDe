import {
  Body,
  Controller,
  Get,
  Headers,
  HttpCode,
  HttpStatus,
  Post,
  Req,
  UseGuards,
} from '@nestjs/common';
import { Throttle } from '@nestjs/throttler';
import type { Request } from 'express';
import { AuthService } from './auth.service';
import { AuthModeService } from './auth-mode.service';
import type { AuthTokenPair } from './auth.models';
import { PasswordResetService } from './password-reset.service';
import {
  SessionAuthGuard,
  type AuthenticatedRequest,
} from './session-auth.guard';
import { SessionService } from './session.service';
import { ConfirmEmailDto } from './dto/confirm-email.dto';
import { ConfirmPasswordResetDto } from './dto/confirm-password-reset.dto';
import { LoginDto } from './dto/login.dto';
import { RefreshSessionDto } from './dto/refresh-session.dto';
import { RegisterDto } from './dto/register.dto';
import { RequestPasswordResetDto } from './dto/request-password-reset.dto';
import { ResendVerificationDto } from './dto/resend-verification.dto';

@Controller('api/v1/auth')
export class AuthController {
  constructor(
    private readonly auth: AuthService,
    private readonly sessions: SessionService,
    private readonly passwordReset: PasswordResetService,
    private readonly authMode: AuthModeService,
  ) {}

  @Get('capabilities')
  capabilities() {
    return this.authMode.capabilities();
  }

  @Post('register')
  @HttpCode(HttpStatus.ACCEPTED)
  @Throttle({ default: { limit: 5, ttl: 60_000 } })
  register(
    @Body() input: RegisterDto,
    @Headers('x-tide-session-id') analyticsSessionId?: string,
  ) {
    this.authMode.assertLocalAuthEnabled();
    return this.auth.register(input, analyticsSessionId);
  }

  @Post('email-verification/confirm')
  @HttpCode(HttpStatus.OK)
  confirmEmail(
    @Body() input: ConfirmEmailDto,
    @Headers('x-tide-session-id') analyticsSessionId?: string,
  ) {
    this.authMode.assertLocalAuthEnabled();
    return this.auth.confirmEmail(input.token, analyticsSessionId);
  }

  @Post('email-verification/resend')
  @HttpCode(HttpStatus.ACCEPTED)
  @Throttle({ default: { limit: 5, ttl: 60_000 } })
  resendVerification(@Body() input: ResendVerificationDto) {
    this.authMode.assertLocalAuthEnabled();
    return this.auth.resendVerification(input.email);
  }

  @Post('login')
  @HttpCode(HttpStatus.OK)
  @Throttle({ default: { limit: 10, ttl: 60_000 } })
  login(
    @Body() input: LoginDto,
    @Req() request: Request,
    @Headers('x-tide-session-id') analyticsSessionId?: string,
  ): Promise<AuthTokenPair> {
    this.authMode.assertLocalAuthEnabled();
    return this.sessions.login({
      ...input,
      ipAddress: this.ipAddress(request),
      deviceSummary: this.deviceSummary(request),
      analyticsSessionId,
    });
  }

  @Post('refresh')
  @HttpCode(HttpStatus.OK)
  @Throttle({ default: { limit: 20, ttl: 60_000 } })
  refresh(
    @Body() input: RefreshSessionDto,
    @Req() request: Request,
  ): Promise<AuthTokenPair> {
    return this.sessions.refresh({
      refreshToken: input.refreshToken,
      ipAddress: this.ipAddress(request),
      deviceSummary: this.deviceSummary(request),
    });
  }

  @Post('logout')
  @HttpCode(HttpStatus.NO_CONTENT)
  @UseGuards(SessionAuthGuard)
  async logout(@Req() request: AuthenticatedRequest): Promise<void> {
    await this.sessions.logout(request.auth);
  }

  @Post('password-reset/request')
  @HttpCode(HttpStatus.ACCEPTED)
  @Throttle({ default: { limit: 5, ttl: 60_000 } })
  requestPasswordReset(
    @Body() input: RequestPasswordResetDto,
    @Headers('x-tide-session-id') analyticsSessionId?: string,
  ) {
    this.authMode.assertLocalAuthEnabled();
    return this.passwordReset.request(input.email, analyticsSessionId);
  }

  @Post('password-reset/confirm')
  @HttpCode(HttpStatus.OK)
  @Throttle({ default: { limit: 5, ttl: 60_000 } })
  confirmPasswordReset(
    @Body() input: ConfirmPasswordResetDto,
    @Headers('x-tide-session-id') analyticsSessionId?: string,
  ) {
    this.authMode.assertLocalAuthEnabled();
    return this.passwordReset.confirm(
      input.token,
      input.newPassword,
      analyticsSessionId,
    );
  }

  private ipAddress(request: Request): string | null {
    return request.ip || request.socket.remoteAddress || null;
  }

  private deviceSummary(request: Request): string | null {
    const userAgent = request.headers['user-agent'];
    return typeof userAgent === 'string' ? userAgent.slice(0, 512) : null;
  }
}
