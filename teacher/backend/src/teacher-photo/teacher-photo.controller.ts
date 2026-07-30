import {
  Controller,
  Get,
  Headers,
  HttpCode,
  HttpStatus,
  Param,
  Post,
  Req,
  Res,
  StreamableFile,
  UploadedFile,
  UseGuards,
  UseInterceptors,
} from '@nestjs/common';
import { FileInterceptor } from '@nestjs/platform-express';
import type { Response } from 'express';
import {
  SessionAuthGuard,
  type AuthenticatedRequest,
} from '../auth/session-auth.guard';
import { TeacherPhotoService } from './teacher-photo.service';

@Controller('api/v1/tasks/:taskInstanceId/teacher-photo')
@UseGuards(SessionAuthGuard)
export class TeacherPhotoController {
  constructor(private readonly photos: TeacherPhotoService) {}

  @Post()
  @HttpCode(HttpStatus.ACCEPTED)
  @UseInterceptors(
    FileInterceptor('photo', {
      limits: { fileSize: 10 * 1024 * 1024, files: 1 },
    }),
  )
  submit(
    @Req() request: AuthenticatedRequest,
    @Param('taskInstanceId') taskInstanceId: string,
    @Headers('idempotency-key') idempotencyKey: string | undefined,
    @UploadedFile() photo: Express.Multer.File | undefined,
  ) {
    return this.photos.submit(
      request.auth,
      taskInstanceId,
      idempotencyKey,
      photo,
    );
  }

  @Get()
  latest(
    @Req() request: AuthenticatedRequest,
    @Param('taskInstanceId') taskInstanceId: string,
  ) {
    return this.photos.latest(request.auth, taskInstanceId);
  }

  @Get('content')
  async content(
    @Req() request: AuthenticatedRequest,
    @Param('taskInstanceId') taskInstanceId: string,
    @Res({ passthrough: true }) response: Response,
  ): Promise<StreamableFile> {
    const file = await this.photos.content(request.auth, taskInstanceId);
    response.setHeader('Content-Type', file.mimeType);
    response.setHeader(
      'Content-Disposition',
      `inline; filename="teacher-standard-photo.jpg"; filename*=UTF-8''${encodeURIComponent(file.filename)}`,
    );
    response.setHeader('Cache-Control', 'private, no-store');
    return new StreamableFile(file.content);
  }
}
