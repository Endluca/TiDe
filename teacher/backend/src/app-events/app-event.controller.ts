import {
  Body,
  Controller,
  HttpCode,
  HttpStatus,
  Post,
  Req,
  UseGuards,
} from '@nestjs/common';
import { Throttle } from '@nestjs/throttler';
import {
  SessionAuthGuard,
  type AuthenticatedRequest,
} from '../auth/session-auth.guard';
import { AppEventService } from './app-event.service';
import { CreateAppEventDto } from './dto/create-app-event.dto';
import { CreateAppEventBatchDto } from './dto/create-app-event-batch.dto';

@Controller('api/v1/app-events')
export class AppEventController {
  constructor(private readonly events: AppEventService) {}

  @Post()
  @HttpCode(HttpStatus.ACCEPTED)
  @Throttle({ default: { limit: 300, ttl: 60_000 } })
  @UseGuards(SessionAuthGuard)
  save(@Req() request: AuthenticatedRequest, @Body() input: CreateAppEventDto) {
    return this.events.save(request.auth, input);
  }

  @Post('batch')
  @HttpCode(HttpStatus.ACCEPTED)
  @Throttle({ default: { limit: 60, ttl: 60_000 } })
  @UseGuards(SessionAuthGuard)
  saveBatch(
    @Req() request: AuthenticatedRequest,
    @Body() input: CreateAppEventBatchDto,
  ) {
    return this.events.saveBatch(request.auth, input.events);
  }

  @Post('anonymous')
  @HttpCode(HttpStatus.ACCEPTED)
  @Throttle({ default: { limit: 30, ttl: 60_000 } })
  saveAnonymous(@Body() input: CreateAppEventDto) {
    return this.events.saveAnonymous(input);
  }

  @Post('anonymous/batch')
  @HttpCode(HttpStatus.ACCEPTED)
  @Throttle({ default: { limit: 20, ttl: 60_000 } })
  saveAnonymousBatch(@Body() input: CreateAppEventBatchDto) {
    return this.events.saveAnonymousBatch(input.events);
  }
}
