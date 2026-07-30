import { Injectable } from '@nestjs/common';
import { randomUUID } from 'node:crypto';
import type { PoolClient, QueryResultRow } from 'pg';
import { DatabaseService } from '../platform/database/database.service';
import type {
  FaqAnswerResponse,
  FaqConversation,
  FaqConversationCreated,
  FaqFeedbackResponse,
  FaqKnowledgeChunk,
  FaqMessage,
  RankedFaqChunk,
} from './faq.models';

interface BindingRow extends QueryResultRow {
  bindingId: string;
}

interface ConversationRow extends QueryResultRow {
  conversationId: string;
  status: FaqConversation['status'];
  startedAt: Date;
}

interface MessageRow extends QueryResultRow {
  id: string;
  role: FaqMessage['role'];
  body: string;
  faqHit: boolean;
  reasonCode: string | null;
  createdAt: Date;
  feedbackResolved: boolean | null;
  feedbackReasonCode: string | null;
}

interface KnowledgeRow extends QueryResultRow, FaqKnowledgeChunk {}

interface PromptVersionRow extends QueryResultRow {
  promptVersionId: string;
}

interface CommandRow extends QueryResultRow {
  conversationId: string;
  requestHash: string;
  responseBody: FaqAnswerResponse;
}

interface FeedbackRow extends QueryResultRow {
  resolved: boolean;
  reasonCode: string | null;
}

export class FaqNotFoundError extends Error {}
export class FaqCommandConflictError extends Error {}
export class FaqFeedbackConflictError extends Error {}

@Injectable()
export class FaqRepository {
  constructor(private readonly database: DatabaseService) {}

  transaction<Result>(
    work: (client: PoolClient) => Promise<Result>,
  ): Promise<Result> {
    return this.database.withTideTransaction(work);
  }

  async createConversation(accountId: string): Promise<FaqConversationCreated> {
    return this.transaction(async (client) => {
      const bindingId = await this.findBindingId(client, accountId, true);
      if (!bindingId) {
        throw new FaqNotFoundError();
      }
      const existing = await client.query<ConversationRow>(
        `
          SELECT id AS "conversationId", status, started_at AS "startedAt"
          FROM tide.qa_conversations
          WHERE teacher_binding_id = $1 AND status = 'OPEN'
          ORDER BY started_at DESC, id DESC
          LIMIT 1
        `,
        [bindingId],
      );
      if (existing.rows[0]) {
        return {
          conversationId: existing.rows[0].conversationId,
          status: 'OPEN',
          startedAt: existing.rows[0].startedAt.toISOString(),
        };
      }
      const result = await client.query<ConversationRow>(
        `
          INSERT INTO tide.qa_conversations (id, teacher_binding_id)
          VALUES ($1, $2)
          RETURNING id AS "conversationId", status, started_at AS "startedAt"
        `,
        [randomUUID(), bindingId],
      );
      return {
        conversationId: result.rows[0].conversationId,
        status: 'OPEN',
        startedAt: result.rows[0].startedAt.toISOString(),
      };
    });
  }

  async getConversation(
    accountId: string,
    conversationId: string,
  ): Promise<FaqConversation | null> {
    const conversation = await this.database.queryTide<ConversationRow>(
      `
        SELECT conversation.id AS "conversationId", conversation.status,
               conversation.started_at AS "startedAt"
        FROM tide.qa_conversations conversation
        JOIN tide.teacher_bindings binding
          ON binding.id = conversation.teacher_binding_id
        WHERE conversation.id = $1 AND binding.account_id = $2
          AND binding.status = 'ACTIVE'
        LIMIT 1
      `,
      [conversationId, accountId],
    );
    if (!conversation.rows[0]) {
      return null;
    }
    const messages = await this.database.queryTide<MessageRow>(
      `
        SELECT
          message.id, message.role, message.body,
          message.faq_hit AS "faqHit", message.reason_code AS "reasonCode",
          message.created_at AS "createdAt",
          feedback.resolved AS "feedbackResolved",
          feedback.reason_code AS "feedbackReasonCode"
        FROM tide.qa_messages message
        LEFT JOIN tide.qa_feedback feedback ON feedback.message_id = message.id
        WHERE message.conversation_id = $1
        ORDER BY message.created_at, message.id
      `,
      [conversationId],
    );
    return {
      conversationId,
      status: conversation.rows[0].status,
      startedAt: conversation.rows[0].startedAt.toISOString(),
      messages: messages.rows.map((row) => this.toMessage(row)),
    };
  }

  async lockOwnedConversation(
    client: PoolClient,
    accountId: string,
    conversationId: string,
  ): Promise<void> {
    const result = await client.query(
      `
        SELECT 1
        FROM tide.qa_conversations conversation
        JOIN tide.teacher_bindings binding
          ON binding.id = conversation.teacher_binding_id
        WHERE conversation.id = $1 AND binding.account_id = $2
          AND binding.status = 'ACTIVE' AND conversation.status = 'OPEN'
        FOR UPDATE OF conversation
      `,
      [conversationId, accountId],
    );
    if (result.rowCount === 0) {
      throw new FaqNotFoundError();
    }
  }

  async findCommandReplay(
    client: PoolClient,
    input: {
      accountId: string;
      conversationId: string;
      idempotencyKey: string;
      requestHash: string;
    },
  ): Promise<FaqAnswerResponse | null> {
    await client.query(
      `SELECT pg_advisory_xact_lock(hashtextextended($1, 0))`,
      [`faq-message:${input.accountId}:${input.idempotencyKey}`],
    );
    const result = await client.query<CommandRow>(
      `
        SELECT conversation_id AS "conversationId",
               request_hash AS "requestHash", response_body AS "responseBody"
        FROM tide.qa_message_commands
        WHERE account_id = $1 AND idempotency_key = $2
        LIMIT 1
      `,
      [input.accountId, input.idempotencyKey],
    );
    const existing = result.rows[0];
    if (!existing) {
      return null;
    }
    if (
      existing.conversationId !== input.conversationId ||
      existing.requestHash !== input.requestHash
    ) {
      throw new FaqCommandConflictError();
    }
    return existing.responseBody;
  }

  saveCommandReceipt(
    client: PoolClient,
    input: {
      accountId: string;
      conversationId: string;
      idempotencyKey: string;
      requestHash: string;
      response: FaqAnswerResponse;
    },
  ): Promise<unknown> {
    return client.query(
      `
        INSERT INTO tide.qa_message_commands (
          id, account_id, conversation_id, idempotency_key,
          request_hash, response_body
        ) VALUES ($1, $2, $3, $4, $5, $6)
      `,
      [
        randomUUID(),
        input.accountId,
        input.conversationId,
        input.idempotencyKey,
        input.requestHash,
        input.response,
      ],
    );
  }

  async loadActiveKnowledge(client: PoolClient): Promise<FaqKnowledgeChunk[]> {
    const result = await client.query<KnowledgeRow>(
      `
        SELECT
          chunk.id, document.title, chunk.chunk_key AS section,
          chunk.body, chunk.retrieval_metadata AS metadata
        FROM tide.knowledge_chunks chunk
        JOIN tide.knowledge_documents document ON document.id = chunk.document_id
        WHERE document.status = 'ACTIVE'
          AND upper(document.authority_level) = 'FAQ'
          AND COALESCE(chunk.retrieval_metadata->>'answerable', 'true') = 'true'
        ORDER BY document.activated_at DESC NULLS LAST,
                 document.version DESC, chunk.position
        LIMIT 2000
      `,
    );
    return result.rows;
  }

  async loadActivePromptVersion(
    client: PoolClient,
    capability: string,
  ): Promise<string | null> {
    const result = await client.query<PromptVersionRow>(
      `
        SELECT id AS "promptVersionId"
        FROM tide.ai_prompt_versions
        WHERE capability = $1 AND status = 'ACTIVE'
        ORDER BY activated_at DESC NULLS LAST, version DESC
        LIMIT 1
      `,
      [capability],
    );
    return result.rows[0]?.promptVersionId ?? null;
  }

  async insertMessage(
    client: PoolClient,
    input: {
      conversationId: string;
      role: FaqMessage['role'];
      body: string;
      faqHit: boolean;
      aiRunId: string | null;
      reasonCode: string | null;
    },
  ): Promise<FaqMessage> {
    const result = await client.query<MessageRow>(
      `
        INSERT INTO tide.qa_messages (
          id, conversation_id, role, body, faq_hit, ai_run_id, reason_code
        ) VALUES ($1, $2, $3, $4, $5, $6, $7)
        RETURNING id, role, body, faq_hit AS "faqHit",
                  reason_code AS "reasonCode", created_at AS "createdAt"
      `,
      [
        randomUUID(),
        input.conversationId,
        input.role,
        input.body,
        input.faqHit,
        input.aiRunId,
        input.reasonCode,
      ],
    );
    return this.toMessage(result.rows[0]);
  }

  async linkSources(
    client: PoolClient,
    messageId: string,
    sources: RankedFaqChunk[],
  ): Promise<void> {
    for (const source of sources) {
      await client.query(
        `
          INSERT INTO tide.qa_source_links (
            message_id, knowledge_chunk_id, position
          ) VALUES ($1, $2, $3)
        `,
        [messageId, source.id, source.position],
      );
    }
  }

  upsertGap(client: PoolClient, normalizedQuestion: string): Promise<unknown> {
    return client.query(
      `
        INSERT INTO tide.faq_gaps (id, normalized_question)
        VALUES ($1, $2)
        ON CONFLICT (normalized_question)
        DO UPDATE SET occurrence_count = tide.faq_gaps.occurrence_count + 1,
                      last_seen_at = now()
      `,
      [randomUUID(), normalizedQuestion],
    );
  }

  async saveFeedback(
    accountId: string,
    messageId: string,
    resolved: boolean,
    reasonCode: string | null,
  ): Promise<FaqFeedbackResponse> {
    return this.transaction(async (client) => {
      const bindingId = await this.findBindingId(client, accountId);
      if (!bindingId) {
        throw new FaqNotFoundError();
      }
      const message = await client.query(
        `
          SELECT 1
          FROM tide.qa_messages message
          JOIN tide.qa_conversations conversation ON conversation.id = message.conversation_id
          WHERE message.id = $1 AND message.role = 'ASSISTANT'
            AND conversation.teacher_binding_id = $2
          LIMIT 1
        `,
        [messageId, bindingId],
      );
      if (message.rowCount === 0) {
        throw new FaqNotFoundError();
      }
      const existing = await client.query<FeedbackRow>(
        `SELECT resolved, reason_code AS "reasonCode" FROM tide.qa_feedback WHERE message_id = $1`,
        [messageId],
      );
      if (existing.rows[0]) {
        if (
          existing.rows[0].resolved !== resolved ||
          existing.rows[0].reasonCode !== reasonCode
        ) {
          throw new FaqFeedbackConflictError();
        }
      } else {
        await client.query(
          `
            INSERT INTO tide.qa_feedback (
              id, message_id, teacher_binding_id, resolved, reason_code
            ) VALUES ($1, $2, $3, $4, $5)
          `,
          [randomUUID(), messageId, bindingId, resolved, reasonCode],
        );
      }
      return { messageId, resolved, reasonCode };
    });
  }

  private async findBindingId(
    client: PoolClient,
    accountId: string,
    lock = false,
  ): Promise<string | null> {
    const result = await client.query<BindingRow>(
      `
        SELECT id AS "bindingId"
        FROM tide.teacher_bindings
        WHERE account_id = $1 AND status = 'ACTIVE'
        LIMIT 1
        ${lock ? 'FOR UPDATE' : ''}
      `,
      [accountId],
    );
    return result.rows[0]?.bindingId ?? null;
  }

  private toMessage(row: MessageRow): FaqMessage {
    return {
      id: row.id,
      role: row.role,
      body: row.body,
      faqHit: row.faqHit,
      reasonCode: row.reasonCode,
      feedback:
        typeof row.feedbackResolved === 'boolean'
          ? {
              resolved: row.feedbackResolved,
              reasonCode: row.feedbackReasonCode,
            }
          : null,
      createdAt: row.createdAt.toISOString(),
    };
  }
}
