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
  Req,
  Res,
  StreamableFile,
  UploadedFiles,
  UseGuards,
  UseInterceptors,
} from '@nestjs/common';
import { FilesInterceptor } from '@nestjs/platform-express';
import type { Response } from 'express';
import {
  SessionAuthGuard,
  type AuthenticatedRequest,
} from '../auth/session-auth.guard';
import {
  CreateSupportTicketDto,
  ReplySupportTicketDto,
  ResolveSupportTicketDto,
} from './dto/create-support-ticket.dto';
import { SupportTicketService } from './support-ticket.service';

@Controller('api/v1/support-tickets')
@UseGuards(SessionAuthGuard)
export class SupportTicketController {
  constructor(private readonly tickets: SupportTicketService) {}

  @Get()
  list(@Req() request: AuthenticatedRequest) {
    return this.tickets.list(request.auth);
  }

  @Post()
  @UseInterceptors(
    FilesInterceptor('images', 3, {
      limits: { files: 3, fileSize: 8 * 1024 * 1024 },
    }),
  )
  create(
    @Req() request: AuthenticatedRequest,
    @Headers('user-agent') userAgent: string | undefined,
    @Body() input: CreateSupportTicketDto,
    @UploadedFiles() images: Express.Multer.File[] | undefined,
  ) {
    return this.tickets.create(request.auth, input, images, userAgent);
  }

  @Get(':ticketId')
  get(
    @Req() request: AuthenticatedRequest,
    @Param('ticketId', new ParseUUIDPipe()) ticketId: string,
  ) {
    return this.tickets.get(request.auth, ticketId);
  }

  @Post(':ticketId/read')
  @HttpCode(HttpStatus.OK)
  markRead(
    @Req() request: AuthenticatedRequest,
    @Param('ticketId', new ParseUUIDPipe()) ticketId: string,
  ) {
    return this.tickets.markRead(request.auth, ticketId);
  }

  @Post(':ticketId/messages')
  @HttpCode(HttpStatus.OK)
  @UseInterceptors(
    FilesInterceptor('images', 3, {
      limits: { files: 3, fileSize: 8 * 1024 * 1024 },
    }),
  )
  reply(
    @Req() request: AuthenticatedRequest,
    @Param('ticketId', new ParseUUIDPipe()) ticketId: string,
    @Body() input: ReplySupportTicketDto,
    @UploadedFiles() images: Express.Multer.File[] | undefined,
  ) {
    return this.tickets.reply(request.auth, ticketId, input, images);
  }

  @Post(':ticketId/resolve')
  @HttpCode(HttpStatus.OK)
  resolve(
    @Req() request: AuthenticatedRequest,
    @Param('ticketId', new ParseUUIDPipe()) ticketId: string,
    @Body() input: ResolveSupportTicketDto,
  ) {
    return this.tickets.resolve(request.auth, ticketId, input.rowVersion);
  }

  @Get(':ticketId/images/:fileId')
  async image(
    @Req() request: AuthenticatedRequest,
    @Param('ticketId', new ParseUUIDPipe()) ticketId: string,
    @Param('fileId', new ParseUUIDPipe()) fileId: string,
    @Res({ passthrough: true }) response: Response,
  ): Promise<StreamableFile> {
    const image = await this.tickets.image(request.auth, ticketId, fileId);
    response.setHeader('Content-Type', image.mimeType);
    response.setHeader(
      'Content-Disposition',
      `inline; filename="ticket-image"; filename*=UTF-8''${encodeURIComponent(image.filename)}`,
    );
    response.setHeader('Cache-Control', 'private, no-store');
    return new StreamableFile(image.content);
  }
}
