import {
  BadRequestException,
  ConflictException,
  Injectable,
  NotFoundException,
  Optional,
} from '@nestjs/common';
import { createHash } from 'node:crypto';
import type { PoolClient } from 'pg';
import { z } from 'zod';
import type { AuthPrincipal } from '../auth/auth.models';
import { AppEventService } from '../app-events/app-event.service';
import { AiGatewayService } from '../integrations/ai/ai-gateway.service';
import type { AskFaqDto } from './dto/ask-faq.dto';
import type { FaqFeedbackDto } from './dto/faq-feedback.dto';
import type {
  FaqAnswerResponse,
  FaqConversation,
  FaqConversationCreated,
  FaqFeedbackResponse,
  FaqKnowledgeChunk,
  FaqMessage,
  RankedFaqChunk,
} from './faq.models';
import {
  FaqCommandConflictError,
  FaqFeedbackConflictError,
  FaqNotFoundError,
  FaqRepository,
} from './faq.repository';
import {
  buildFaqIntentMatchSystemPrompt,
  buildFaqSystemPrompt,
} from './faq-prompt';
import { FaqRetriever } from './faq-retriever';

const modelAnswerSchema = z
  .object({
    decision: z.enum(['MATCHED', 'CLARIFY', 'NOT_FOUND']),
    answer: z.string().max(4_000),
    clarificationQuestion: z.string().max(1_000),
    usedSourcePositions: z.array(z.number().int().positive()).max(4),
  })
  .strict();

const intentMatchSchema = z
  .object({
    decision: z.enum(['MATCHED', 'CLARIFY', 'NOT_FOUND']),
    faqIds: z.array(z.string().min(1).max(128)).max(4),
    clarificationQuestion: z.string().max(1_000),
  })
  .strict();

@Injectable()
export class FaqService {
  constructor(
    private readonly repository: FaqRepository,
    private readonly retriever: FaqRetriever,
    private readonly gateway: AiGatewayService,
    @Optional() private readonly events?: AppEventService,
  ) {}

  async createConversation(
    principal: AuthPrincipal,
  ): Promise<FaqConversationCreated> {
    try {
      return await this.repository.createConversation(principal.accountId);
    } catch (error) {
      this.rethrow(error);
    }
  }

  async getConversation(
    principal: AuthPrincipal,
    conversationId: string,
  ): Promise<FaqConversation> {
    const conversation = await this.repository.getConversation(
      principal.accountId,
      conversationId,
    );
    if (!conversation) {
      throw this.notFound();
    }
    return this.toPublicConversation(conversation);
  }

  async ask(
    principal: AuthPrincipal,
    conversationId: string,
    idempotencyKey: string | undefined,
    input: AskFaqDto,
    analyticsSessionId?: string,
  ): Promise<FaqAnswerResponse> {
    const key = this.requireIdempotencyKey(idempotencyKey);
    const question = input.message.trim();
    if (question.length < 2) {
      throw new BadRequestException({
        code: 'VALIDATION_FAILED',
        message: '问题内容不能为空',
        retryable: false,
      });
    }
    const requestHash = createHash('sha256')
      .update(JSON.stringify({ question }))
      .digest('hex');

    try {
      const response = await this.repository.transaction(async (client) => {
        const replay = await this.repository.findCommandReplay(client, {
          accountId: principal.accountId,
          conversationId,
          idempotencyKey: key,
          requestHash,
        });
        if (replay) {
          return this.toPublicResponse(replay);
        }
        await this.repository.lockOwnedConversation(
          client,
          principal.accountId,
          conversationId,
        );
        const teacherMessage = await this.repository.insertMessage(client, {
          conversationId,
          role: 'TEACHER',
          body: question,
          faqHit: false,
          aiRunId: null,
          reasonCode: null,
        });
        const knowledge = await this.repository.loadActiveKnowledge(client);
        const ranked = this.retriever.rank(question, knowledge);
        const answer =
          ranked.length === 0
            ? await this.matchCatalogAndAnswer(
                client,
                conversationId,
                question,
                knowledge,
              )
            : await this.answerFromSources(
                client,
                conversationId,
                question,
                ranked,
                knowledge,
                true,
              );
        const response = this.toPublicResponse({
          conversationId,
          teacherMessage,
          answer,
        });
        await this.repository.saveCommandReceipt(client, {
          accountId: principal.accountId,
          conversationId,
          idempotencyKey: key,
          requestHash,
          response,
        });
        return response;
      });
      const matched = response.answer.faqHit === true;
      this.events?.captureSystem({
        eventName: matched ? 'FAQ_MATCHED' : 'FAQ_NOT_MATCHED',
        eventId: `server-event-${createHash('sha256')
          .update(`faq:${principal.accountId}:${key}`)
          .digest('hex')}`,
        accountId: principal.accountId,
        sessionId:
          analyticsSessionId &&
          analyticsSessionId.length >= 8 &&
          analyticsSessionId.length <= 128
            ? analyticsSessionId
            : principal.sessionId,
        properties: {
          faqHit: matched,
          result: matched ? 'MATCHED' : 'NOT_MATCHED',
          ...(response.answer.reasonCode
            ? { reasonCode: response.answer.reasonCode }
            : {}),
        },
      });
      return response;
    } catch (error) {
      this.rethrow(error);
    }
  }

  async saveFeedback(
    principal: AuthPrincipal,
    messageId: string,
    input: FaqFeedbackDto,
  ): Promise<FaqFeedbackResponse> {
    try {
      return await this.repository.saveFeedback(
        principal.accountId,
        messageId,
        input.resolved,
        input.reasonCode?.trim() || null,
      );
    } catch (error) {
      this.rethrow(error);
    }
  }

  private async answerFromSources(
    client: PoolClient,
    conversationId: string,
    question: string,
    sources: RankedFaqChunk[],
    knowledge: FaqKnowledgeChunk[],
    allowCatalogFallback: boolean,
  ): Promise<FaqMessage> {
    const promptVersionId = await this.repository.loadActivePromptVersion(
      client,
      'FAQ_TEXT_ANSWER',
    );
    const execution = await this.gateway.execute({
      capability: 'FAQ_TEXT_ANSWER',
      callerModule: 'FAQ',
      promptVersionId,
      systemPrompt: buildFaqSystemPrompt(sources),
      userText: question,
    });
    if (execution.status === 'FAILED') {
      return this.saveMatchingFailure(
        client,
        conversationId,
        execution.aiRunId,
        'FAQ_AI_UNAVAILABLE',
        question,
      );
    }
    const parsed = this.parseModelAnswer(execution.content, sources);
    if (!parsed) {
      return this.saveMatchingFailure(
        client,
        conversationId,
        execution.aiRunId,
        'FAQ_AI_RESPONSE_INVALID',
        question,
      );
    }
    if (parsed.decision === 'CLARIFY') {
      return this.saveClarification(
        client,
        conversationId,
        parsed.clarificationQuestion,
        execution.aiRunId,
      );
    }
    if (parsed.decision === 'NOT_FOUND') {
      return allowCatalogFallback
        ? this.matchCatalogAndAnswer(
            client,
            conversationId,
            question,
            knowledge,
          )
        : this.saveNoMatch(client, conversationId, question, execution.aiRunId);
    }
    const usedSources = sources.filter((source) =>
      parsed.usedSourcePositions.includes(source.position),
    );
    const displayAnswer = this.toDisplayMarkdown(parsed.answer);
    if (!displayAnswer) {
      return this.saveMatchingFailure(
        client,
        conversationId,
        execution.aiRunId,
        'FAQ_AI_RESPONSE_INVALID',
        question,
      );
    }
    const answer = await this.repository.insertMessage(client, {
      conversationId,
      role: 'ASSISTANT',
      body: displayAnswer,
      faqHit: true,
      aiRunId: execution.aiRunId,
      reasonCode: 'FAQ_MATCHED',
    });
    await this.repository.linkSources(client, answer.id, usedSources);
    return answer;
  }

  private async matchCatalogAndAnswer(
    client: PoolClient,
    conversationId: string,
    question: string,
    knowledge: FaqKnowledgeChunk[],
  ): Promise<FaqMessage> {
    const promptVersionId = await this.repository.loadActivePromptVersion(
      client,
      'FAQ_INTENT_MATCH',
    );
    const execution = await this.gateway.execute({
      capability: 'FAQ_INTENT_MATCH',
      callerModule: 'FAQ',
      promptVersionId,
      systemPrompt: buildFaqIntentMatchSystemPrompt(knowledge),
      userText: question,
    });
    if (execution.status === 'FAILED') {
      return this.saveMatchingFailure(
        client,
        conversationId,
        execution.aiRunId,
        'FAQ_AI_UNAVAILABLE',
        question,
      );
    }
    const match = this.parseIntentMatch(execution.content, knowledge);
    if (!match) {
      return this.saveMatchingFailure(
        client,
        conversationId,
        execution.aiRunId,
        'FAQ_AI_RESPONSE_INVALID',
        question,
      );
    }
    if (match.decision === 'CLARIFY') {
      return this.saveClarification(
        client,
        conversationId,
        match.clarificationQuestion,
        execution.aiRunId,
      );
    }
    if (match.decision === 'NOT_FOUND') {
      return this.saveNoMatch(
        client,
        conversationId,
        question,
        execution.aiRunId,
      );
    }
    const byId = new Map(
      knowledge.map((chunk) => [this.catalogFaqId(chunk), chunk]),
    );
    const selected = match.faqIds
      .map((faqId) => byId.get(faqId))
      .filter((chunk): chunk is FaqKnowledgeChunk => chunk !== undefined)
      .map((chunk, index) => ({
        ...chunk,
        position: index + 1,
        score: 1,
      }));
    if (selected.length !== match.faqIds.length || selected.length === 0) {
      return this.saveMatchingFailure(
        client,
        conversationId,
        execution.aiRunId,
        'FAQ_AI_RESPONSE_INVALID',
        question,
      );
    }
    return this.answerFromSources(
      client,
      conversationId,
      question,
      selected,
      knowledge,
      false,
    );
  }

  private async saveNoMatch(
    client: PoolClient,
    conversationId: string,
    question: string,
    aiRunId: string | null = null,
  ): Promise<FaqMessage> {
    await this.repository.upsertGap(
      client,
      this.retriever.normalizeQuestion(question),
    );
    return this.repository.insertMessage(client, {
      conversationId,
      role: 'ASSISTANT',
      body: this.noMatchMessage(question),
      faqHit: false,
      aiRunId,
      reasonCode: 'FAQ_NOT_FOUND',
    });
  }

  private saveClarification(
    client: PoolClient,
    conversationId: string,
    clarificationQuestion: string,
    aiRunId: string,
  ): Promise<FaqMessage> {
    return this.repository.insertMessage(client, {
      conversationId,
      role: 'ASSISTANT',
      body: clarificationQuestion,
      faqHit: false,
      aiRunId,
      reasonCode: 'FAQ_CLARIFICATION_NEEDED',
    });
  }

  private saveMatchingFailure(
    client: PoolClient,
    conversationId: string,
    aiRunId: string | null,
    reasonCode: string,
    question: string,
  ): Promise<FaqMessage> {
    return this.repository.insertMessage(client, {
      conversationId,
      role: 'ASSISTANT',
      body: this.sourceFallbackMessage(question),
      faqHit: false,
      aiRunId,
      reasonCode,
    });
  }

  private parseModelAnswer(
    content: string,
    sources: RankedFaqChunk[],
  ): z.infer<typeof modelAnswerSchema> | null {
    const json = this.parseJson(content);
    if (json === null) return null;
    const parsed = modelAnswerSchema.safeParse(json);
    if (!parsed.success) {
      return null;
    }
    if (parsed.data.decision === 'CLARIFY') {
      return parsed.data.answer.length === 0 &&
        parsed.data.usedSourcePositions.length === 0 &&
        parsed.data.clarificationQuestion.trim().length > 0
        ? parsed.data
        : null;
    }
    if (parsed.data.decision === 'NOT_FOUND') {
      return parsed.data.answer.length === 0 &&
        parsed.data.usedSourcePositions.length === 0 &&
        parsed.data.clarificationQuestion.length === 0
        ? parsed.data
        : null;
    }
    if (parsed.data.clarificationQuestion.length > 0) {
      return null;
    }
    const positions = parsed.data.usedSourcePositions;
    const allowed = new Set(sources.map((source) => source.position));
    const referenced = [...parsed.data.answer.matchAll(/\[Source (\d+)]/g)].map(
      (match) => Number(match[1]),
    );
    if (
      parsed.data.answer.trim().length === 0 ||
      positions.length === 0 ||
      new Set(positions).size !== positions.length ||
      positions.some((position) => !allowed.has(position)) ||
      referenced.some((position) => !allowed.has(position)) ||
      positions.some((position) => !referenced.includes(position))
    ) {
      return null;
    }
    return parsed.data;
  }

  private parseIntentMatch(
    content: string,
    knowledge: FaqKnowledgeChunk[],
  ): z.infer<typeof intentMatchSchema> | null {
    const json = this.parseJson(content);
    if (json === null) return null;
    const parsed = intentMatchSchema.safeParse(json);
    if (!parsed.success) return null;
    if (parsed.data.decision === 'CLARIFY') {
      return parsed.data.faqIds.length === 0 &&
        parsed.data.clarificationQuestion.trim().length > 0
        ? parsed.data
        : null;
    }
    if (parsed.data.decision === 'NOT_FOUND') {
      return parsed.data.faqIds.length === 0 &&
        parsed.data.clarificationQuestion.length === 0
        ? parsed.data
        : null;
    }
    const allowed = new Set(knowledge.map((chunk) => this.catalogFaqId(chunk)));
    return parsed.data.faqIds.length > 0 &&
      new Set(parsed.data.faqIds).size === parsed.data.faqIds.length &&
      parsed.data.faqIds.every((faqId) => allowed.has(faqId)) &&
      parsed.data.clarificationQuestion.length === 0
      ? parsed.data
      : null;
  }

  private parseJson(content: string): unknown {
    try {
      const fenced = content.match(/^```(?:json)?\s*([\s\S]*?)\s*```$/i);
      return JSON.parse(fenced?.[1] ?? content);
    } catch {
      return null;
    }
  }

  private catalogFaqId(chunk: FaqKnowledgeChunk): string {
    return typeof chunk.metadata.faqId === 'string'
      ? chunk.metadata.faqId
      : chunk.section;
  }

  private toPublicConversation(conversation: FaqConversation): FaqConversation {
    return {
      ...conversation,
      messages: conversation.messages.map((message) =>
        this.toPublicMessage(message),
      ),
    };
  }

  private toPublicResponse(response: FaqAnswerResponse): FaqAnswerResponse {
    return {
      ...response,
      teacherMessage: this.toPublicMessage(response.teacherMessage),
      answer: this.toPublicMessage(response.answer),
    };
  }

  private toPublicMessage(message: FaqMessage): FaqMessage {
    const publicMessage = { ...message } as FaqMessage & {
      sources?: unknown;
    };
    delete publicMessage.sources;
    return {
      ...publicMessage,
      body:
        publicMessage.role === 'ASSISTANT'
          ? this.toDisplayMarkdown(publicMessage.body)
          : publicMessage.body,
    };
  }

  private toDisplayMarkdown(value: string): string {
    return value
      .replace(/[ \t]*\[Source\s+\d+]/gi, '')
      .replace(/[ \t]+([,.;:!?])/g, '$1')
      .replace(/[ \t]+\n/g, '\n')
      .replace(/\n{3,}/g, '\n\n')
      .trim();
  }

  private noMatchMessage(question: string): string {
    return this.isChinese(question)
      ? '当前已生效 FAQ 中没有找到足够依据，因此我暂时不能给出公司口径答案。这个问题已记录，供后续补充 FAQ。'
      : 'I could not find this question in the current FAQ, so I cannot give a company answer yet. The question has been recorded for FAQ review.';
  }

  private sourceFallbackMessage(question: string): string {
    return this.isChinese(question)
      ? '暂时无法可靠判断你的问题对应哪条 FAQ，请稍后重试。'
      : 'I could not reliably match your question to the approved FAQ right now. Please try again shortly.';
  }

  private isChinese(value: string): boolean {
    return /[\u3400-\u9fff]/.test(value);
  }

  private requireIdempotencyKey(value: string | undefined): string {
    const key = value?.trim();
    if (!key || key.length < 8 || key.length > 128) {
      throw new BadRequestException({
        code: 'VALIDATION_FAILED',
        message: 'Idempotency-Key 长度必须为 8 到 128 个字符',
        retryable: false,
      });
    }
    return key;
  }

  private rethrow(error: unknown): never {
    if (error instanceof FaqNotFoundError) {
      throw this.notFound();
    }
    if (error instanceof FaqCommandConflictError) {
      throw new ConflictException({
        code: 'IDEMPOTENCY_CONFLICT',
        message: '同一幂等键已用于其他问答请求',
        retryable: false,
      });
    }
    if (error instanceof FaqFeedbackConflictError) {
      throw new ConflictException({
        code: 'FAQ_FEEDBACK_CONFLICT',
        message: '该回答已经提交过不同反馈',
        retryable: false,
      });
    }
    throw error;
  }

  private notFound(): NotFoundException {
    return new NotFoundException({
      code: 'FAQ_RESOURCE_NOT_FOUND',
      message: '问答会话或消息不存在',
      retryable: false,
    });
  }
}
