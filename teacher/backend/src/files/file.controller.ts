import {
  Body,
  Controller,
  Get,
  Headers,
  HttpCode,
  HttpStatus,
  Param,
  ParseUUIDPipe,
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
import { CompleteUploadDto } from './dto/complete-upload.dto';
import { CreateUploadIntentDto } from './dto/create-upload-intent.dto';
import { FileService } from './file.service';

@Controller('api/v1/files')
@UseGuards(SessionAuthGuard)
export class FileController {
  constructor(private readonly files: FileService) {}

  @Post('upload-intents')
  createUploadIntent(
    @Req() request: AuthenticatedRequest,
    @Headers('idempotency-key') idempotencyKey: string | undefined,
    @Body() input: CreateUploadIntentDto,
  ) {
    return this.files.createUploadIntent(request.auth, idempotencyKey, input);
  }

  @Put(':fileId/content')
  uploadStream(
    @Req() request: AuthenticatedRequest,
    @Param('fileId', new ParseUUIDPipe()) fileId: string,
    @Headers('content-type') contentType: string | undefined,
    @Headers('content-length') contentLength: string | undefined,
  ) {
    return this.files.uploadStream(
      request.auth,
      fileId,
      request,
      contentType,
      contentLength,
    );
  }

  @Post(':fileId/complete')
  @HttpCode(HttpStatus.OK)
  completeUpload(
    @Req() request: AuthenticatedRequest,
    @Param('fileId', new ParseUUIDPipe()) fileId: string,
    @Headers('idempotency-key') idempotencyKey: string | undefined,
    @Body() input: CompleteUploadDto,
  ) {
    return this.files.completeUpload(
      request.auth,
      fileId,
      idempotencyKey,
      input,
    );
  }

  @Get(':fileId/content')
  async download(
    @Req() request: AuthenticatedRequest,
    @Param('fileId', new ParseUUIDPipe()) fileId: string,
    @Res({ passthrough: true }) response: Response,
  ): Promise<StreamableFile> {
    const file = await this.files.download(request.auth, fileId);
    const encodedFilename = encodeURIComponent(file.originalFilename);
    response.setHeader('Content-Type', file.mimeType);
    response.setHeader(
      'Content-Disposition',
      `attachment; filename="download"; filename*=UTF-8''${encodedFilename}`,
    );
    response.setHeader('Cache-Control', 'private, no-store');
    return new StreamableFile(file.content);
  }
}
