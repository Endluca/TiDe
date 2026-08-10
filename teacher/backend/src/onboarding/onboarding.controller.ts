import {
  Body,
  Controller,
  Get,
  Header,
  Headers,
  HttpCode,
  HttpStatus,
  Post,
  Req,
  UseGuards,
} from '@nestjs/common';
import {
  SessionAuthGuard,
  type AuthenticatedRequest,
} from '../auth/session-auth.guard';
import { AcknowledgeOnboardingDto } from './dto/acknowledge-onboarding.dto';
import { OnboardingService } from './onboarding.service';

@Controller('api/v1/me/onboarding')
@UseGuards(SessionAuthGuard)
export class OnboardingController {
  constructor(private readonly onboarding: OnboardingService) {}

  @Get()
  @Header('Cache-Control', 'private, no-store')
  getState(@Req() request: AuthenticatedRequest) {
    return this.onboarding.getState(request.auth);
  }

  @Post('acknowledge')
  @HttpCode(HttpStatus.OK)
  @Header('Cache-Control', 'private, no-store')
  acknowledge(
    @Req() request: AuthenticatedRequest,
    @Headers('idempotency-key') idempotencyKey: string | undefined,
    @Body() input: AcknowledgeOnboardingDto,
  ) {
    return this.onboarding.acknowledge(request.auth, idempotencyKey, input);
  }
}
