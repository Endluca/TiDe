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
  UseGuards,
} from '@nestjs/common';
import {
  SessionAuthGuard,
  type AuthenticatedRequest,
} from '../auth/session-auth.guard';
import { AskFaqDto } from './dto/ask-faq.dto';
import { FaqFeedbackDto } from './dto/faq-feedback.dto';
import { FaqService } from './faq.service';

@Controller('api/v1/faq')
@UseGuards(SessionAuthGuard)
export class FaqController {
  constructor(private readonly faq: FaqService) {}

  @Post('conversations')
  createConversation(@Req() request: AuthenticatedRequest) {
    return this.faq.createConversation(request.auth);
  }

  @Get('conversations/:conversationId')
  getConversation(
    @Req() request: AuthenticatedRequest,
    @Param('conversationId', new ParseUUIDPipe()) conversationId: string,
  ) {
    return this.faq.getConversation(request.auth, conversationId);
  }

  @Post('conversations/:conversationId/messages')
  @HttpCode(HttpStatus.OK)
  ask(
    @Req() request: AuthenticatedRequest,
    @Param('conversationId', new ParseUUIDPipe()) conversationId: string,
    @Headers('idempotency-key') idempotencyKey: string | undefined,
    @Headers('x-tide-session-id') analyticsSessionId: string | undefined,
    @Body() input: AskFaqDto,
  ) {
    return this.faq.ask(
      request.auth,
      conversationId,
      idempotencyKey,
      input,
      analyticsSessionId,
    );
  }

  @Post('messages/:messageId/feedback')
  @HttpCode(HttpStatus.OK)
  saveFeedback(
    @Req() request: AuthenticatedRequest,
    @Param('messageId', new ParseUUIDPipe()) messageId: string,
    @Body() input: FaqFeedbackDto,
  ) {
    return this.faq.saveFeedback(request.auth, messageId, input);
  }
}
