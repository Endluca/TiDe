import {
  Body,
  Controller,
  Get,
  Headers,
  HttpCode,
  HttpStatus,
  Param,
  Post,
  Put,
  Req,
  UseGuards,
} from '@nestjs/common';
import {
  SessionAuthGuard,
  type AuthenticatedRequest,
} from '../auth/session-auth.guard';
import { TaskService } from './task.service';
import { MutationMetaDto } from './dto/mutation-meta.dto';
import { RetryTaskDto } from './dto/retry-task.dto';
import { SaveProgressDto } from './dto/save-progress.dto';
import { SubmitTaskDto } from './dto/submit-task.dto';
import { VideoHeartbeatDto } from './dto/video-heartbeat.dto';

@Controller('api/v1/tasks')
@UseGuards(SessionAuthGuard)
export class TaskController {
  constructor(private readonly tasks: TaskService) {}

  @Get()
  list(@Req() request: AuthenticatedRequest) {
    return this.tasks.list(request.auth);
  }

  @Get(':taskInstanceId')
  get(
    @Req() request: AuthenticatedRequest,
    @Param('taskInstanceId') taskInstanceId: string,
  ) {
    return this.tasks.get(request.auth, taskInstanceId);
  }

  @Post(':taskInstanceId/start')
  @HttpCode(HttpStatus.OK)
  start(
    @Req() request: AuthenticatedRequest,
    @Param('taskInstanceId') taskInstanceId: string,
    @Headers('idempotency-key') idempotencyKey: string | undefined,
    @Headers('x-tide-session-id') analyticsSessionId: string | undefined,
    @Body() input: MutationMetaDto,
  ) {
    return this.tasks.start(
      request.auth,
      taskInstanceId,
      idempotencyKey,
      input,
      analyticsSessionId,
    );
  }

  @Post(':taskInstanceId/view')
  @HttpCode(HttpStatus.OK)
  view(
    @Req() request: AuthenticatedRequest,
    @Param('taskInstanceId') taskInstanceId: string,
    @Headers('idempotency-key') idempotencyKey: string | undefined,
    @Body() input: MutationMetaDto,
  ) {
    return this.tasks.view(request.auth, taskInstanceId, idempotencyKey, input);
  }

  @Put(':taskInstanceId/progress')
  saveProgress(
    @Req() request: AuthenticatedRequest,
    @Param('taskInstanceId') taskInstanceId: string,
    @Headers('idempotency-key') idempotencyKey: string | undefined,
    @Headers('x-tide-session-id') analyticsSessionId: string | undefined,
    @Body() input: SaveProgressDto,
  ) {
    return this.tasks.saveProgress(
      request.auth,
      taskInstanceId,
      idempotencyKey,
      input,
      analyticsSessionId,
    );
  }

  @Put(':taskInstanceId/video-heartbeat')
  videoHeartbeat(
    @Req() request: AuthenticatedRequest,
    @Param('taskInstanceId') taskInstanceId: string,
    @Headers('x-tide-session-id') analyticsSessionId: string | undefined,
    @Body() input: VideoHeartbeatDto,
  ) {
    return this.tasks.saveVideoHeartbeat(
      request.auth,
      taskInstanceId,
      input,
      analyticsSessionId,
    );
  }

  @Post(':taskInstanceId/submissions')
  @HttpCode(HttpStatus.ACCEPTED)
  submit(
    @Req() request: AuthenticatedRequest,
    @Param('taskInstanceId') taskInstanceId: string,
    @Headers('idempotency-key') idempotencyKey: string | undefined,
    @Headers('x-tide-session-id') analyticsSessionId: string | undefined,
    @Body() input: SubmitTaskDto,
  ) {
    return this.tasks.submit(
      request.auth,
      taskInstanceId,
      idempotencyKey,
      input,
      analyticsSessionId,
    );
  }

  @Get(':taskInstanceId/validation')
  getValidation(
    @Req() request: AuthenticatedRequest,
    @Param('taskInstanceId') taskInstanceId: string,
  ) {
    return this.tasks.getValidation(request.auth, taskInstanceId);
  }

  @Post(':taskInstanceId/retry')
  @HttpCode(HttpStatus.OK)
  retry(
    @Req() request: AuthenticatedRequest,
    @Param('taskInstanceId') taskInstanceId: string,
    @Headers('idempotency-key') idempotencyKey: string | undefined,
    @Headers('x-tide-session-id') analyticsSessionId: string | undefined,
    @Body() input: RetryTaskDto,
  ) {
    return this.tasks.retry(
      request.auth,
      taskInstanceId,
      idempotencyKey,
      input,
      analyticsSessionId,
    );
  }
}
