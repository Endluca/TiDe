import {
  Body,
  Controller,
  Get,
  Headers,
  Header,
  HttpCode,
  HttpStatus,
  Param,
  Post,
  Put,
  Req,
  Res,
  StreamableFile,
  UseGuards,
} from '@nestjs/common';
import type { Response } from 'express';
import {
  SessionAuthGuard,
  type AuthenticatedRequest,
} from '../auth/session-auth.guard';
import { TaskService } from './task.service';
import { MutationMetaDto } from './dto/mutation-meta.dto';
import { RetryTaskDto } from './dto/retry-task.dto';
import { RefreshKuozhiProgressDto } from './dto/refresh-kuozhi-progress.dto';
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

  @Get(':taskInstanceId/document-content')
  @Header('Cache-Control', 'private, no-cache')
  documentContent(
    @Req() request: AuthenticatedRequest,
    @Param('taskInstanceId') taskInstanceId: string,
  ) {
    return this.tasks.getDocumentContent(request.auth, taskInstanceId);
  }

  @Get(':taskInstanceId/document-content/assets/:assetKey')
  async documentAsset(
    @Req() request: AuthenticatedRequest,
    @Param('taskInstanceId') taskInstanceId: string,
    @Param('assetKey') assetKey: string,
    @Res({ passthrough: true }) response: Response,
  ) {
    const asset = await this.tasks.getDocumentAsset(
      request.auth,
      taskInstanceId,
      assetKey,
    );
    response.setHeader('Content-Type', asset.contentType);
    response.setHeader('Cache-Control', 'private, max-age=31536000, immutable');
    response.setHeader('ETag', `"${asset.sha256}"`);
    return new StreamableFile(asset.bytes);
  }

  @Get(':taskInstanceId/kuozhi-launch')
  @Header('Cache-Control', 'no-store, max-age=0')
  @Header('Pragma', 'no-cache')
  kuozhiLaunch(
    @Req() request: AuthenticatedRequest,
    @Param('taskInstanceId') taskInstanceId: string,
  ) {
    return this.tasks.getKuozhiLaunch(request.auth, taskInstanceId);
  }

  @Get(':taskInstanceId/kuozhi-progress')
  @Header('Cache-Control', 'no-store, max-age=0')
  @Header('Pragma', 'no-cache')
  kuozhiProgress(
    @Req() request: AuthenticatedRequest,
    @Param('taskInstanceId') taskInstanceId: string,
  ) {
    return this.tasks.getKuozhiProgress(request.auth, taskInstanceId);
  }

  @Post(':taskInstanceId/kuozhi-progress/refresh')
  @HttpCode(HttpStatus.OK)
  refreshKuozhiProgress(
    @Req() request: AuthenticatedRequest,
    @Param('taskInstanceId') taskInstanceId: string,
    @Headers('idempotency-key') idempotencyKey: string | undefined,
    @Body() input: RefreshKuozhiProgressDto,
  ) {
    return this.tasks.refreshKuozhiProgress(
      request.auth,
      taskInstanceId,
      idempotencyKey,
      input,
    );
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
