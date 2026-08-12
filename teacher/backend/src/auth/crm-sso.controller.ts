import {
  Body,
  Controller,
  Get,
  HttpCode,
  HttpStatus,
  Post,
  Query,
  Req,
  Res,
  UnauthorizedException,
} from '@nestjs/common';
import { Throttle } from '@nestjs/throttler';
import type { Request, Response } from 'express';
import { CrmSsoService } from './crm-sso.service';
import { ExchangeCrmSsoDto } from './dto/exchange-crm-sso.dto';

@Controller('api/v1/auth/crm-sso')
export class CrmSsoController {
  constructor(private readonly crmSso: CrmSsoService) {}

  @Get()
  @Throttle({ default: { limit: 20, ttl: 60_000 } })
  async enter(
    @Query() query: Record<string, unknown>,
    @Res() response: Response,
  ): Promise<void> {
    this.secureRedirectResponse(response);
    try {
      const callbackUrl = await this.crmSso.start(
        this.requiredToken(query.token),
        this.optionalRedirect(query.redirect),
      );
      response.redirect(HttpStatus.FOUND, callbackUrl);
    } catch (error) {
      response.redirect(HttpStatus.FOUND, this.crmSso.failureRedirect(error));
    }
  }

  @Post('exchange')
  @HttpCode(HttpStatus.OK)
  @Throttle({ default: { limit: 20, ttl: 60_000 } })
  exchange(@Body() input: ExchangeCrmSsoDto, @Req() request: Request) {
    return this.crmSso.exchange({
      rawCode: input.code,
      ipAddress: request.ip || request.socket.remoteAddress || null,
      deviceSummary:
        typeof request.headers['user-agent'] === 'string'
          ? request.headers['user-agent'].slice(0, 512)
          : null,
    });
  }

  private secureRedirectResponse(response: Response): void {
    response.setHeader('Cache-Control', 'no-store');
    response.setHeader('Pragma', 'no-cache');
    response.setHeader('Referrer-Policy', 'no-referrer');
    response.setHeader('X-Content-Type-Options', 'nosniff');
  }

  private requiredToken(value: unknown): string {
    if (
      typeof value === 'string' &&
      value.length >= 16 &&
      value.length <= 8192
    ) {
      return value;
    }
    throw new UnauthorizedException({
      code: 'CRM_SSO_TOKEN_INVALID',
      message: '登录凭证无效，请从 CRM 重新进入',
      retryable: false,
    });
  }

  private optionalRedirect(value: unknown): string | undefined {
    return typeof value === 'string' && value.length <= 512 ? value : undefined;
  }
}
