import { Injectable } from '@nestjs/common';
import type { PoolClient, QueryResultRow } from 'pg';
import { DatabaseService } from '../platform/database/database.service';
import type {
  SupportTicketImage,
  SupportTicketMessage,
  SupportTicketRow,
} from './support-ticket.models';

interface TeacherBindingRow extends QueryResultRow {
  teacherId: string;
}

interface TicketDatabaseRow extends QueryResultRow, SupportTicketRow {}

interface CleanupTicketRow extends QueryResultRow {
  ticketId: string;
  messages: SupportTicketMessage[];
}

interface OwnedImageRow extends QueryResultRow {
  storageProvider: 'LOCAL' | 'OSS';
  objectKey: string;
  filename: string;
  mimeType: string;
}

export interface StoredSupportImage extends SupportTicketImage {
  file_id: string;
  storage_provider: 'LOCAL' | 'OSS';
  sha256: string;
}

const ticketSelect = `
  ticket_id AS "ticketId",
  teacher_id AS "teacherId",
  primary_category AS "primaryCategory",
  secondary_category AS "secondaryCategory",
  problem_location AS "problemLocation",
  problem_context AS "problemContext",
  messages,
  status,
  last_operator_reply_at AS "lastOperatorReplyAt",
  teacher_reply_deadline_at AS "teacherReplyDeadlineAt",
  last_read_operator_message_id AS "lastReadOperatorMessageId",
  close_reason AS "closeReason",
  closed_at AS "closedAt",
  image_cleanup_status AS "imageCleanupStatus",
  row_version AS "rowVersion",
  created_at AS "createdAt",
  updated_at AS "updatedAt"
`;

@Injectable()
export class SupportTicketRepository {
  constructor(private readonly database: DatabaseService) {}

  async create(input: {
    accountId: string;
    ticketId: string;
    secondaryCategory: string;
    problemLocation: string;
    context: Record<string, unknown>;
    message: Omit<SupportTicketMessage, 'created_at'>;
    images: StoredSupportImage[];
  }): Promise<SupportTicketRow | null> {
    return this.database.withTideTransaction(async (client) => {
      const teacherId = await this.teacherId(client, input.accountId);
      if (!teacherId) return null;
      await this.insertFiles(client, input.accountId, input.images);
      const result = await client.query<TicketDatabaseRow>(
        `
          SELECT ${ticketSelect}
          FROM public.create_teacher_support_ticket(
            $1, $2, $3, $4, $5::jsonb, $6::jsonb
          )
        `,
        [
          input.ticketId,
          teacherId,
          input.secondaryCategory,
          input.problemLocation,
          JSON.stringify(input.context),
          JSON.stringify(input.message),
        ],
      );
      return result.rows[0] ?? null;
    });
  }

  async list(accountId: string): Promise<SupportTicketRow[]> {
    return this.database.withTideTransaction(async (client) => {
      const teacherId = await this.teacherId(client, accountId);
      if (!teacherId) return [];
      await this.reconcileOperatorReplies(client, teacherId);
      const result = await client.query<TicketDatabaseRow>(
        `
          SELECT ${ticketSelect}
          FROM public.teacher_support_tickets
          WHERE teacher_id = $1
          ORDER BY updated_at DESC, ticket_id DESC
          LIMIT 100
        `,
        [teacherId],
      );
      return result.rows;
    });
  }

  async findOwned(
    accountId: string,
    ticketId: string,
  ): Promise<SupportTicketRow | null> {
    return this.database.withTideTransaction(async (client) => {
      const teacherId = await this.teacherId(client, accountId);
      if (!teacherId) return null;
      await this.reconcileOperatorReplies(client, teacherId, ticketId);
      return this.findTicket(client, teacherId, ticketId);
    });
  }

  async appendTeacherMessage(input: {
    accountId: string;
    ticketId: string;
    expectedRowVersion: number;
    message: Omit<SupportTicketMessage, 'created_at'>;
    images: StoredSupportImage[];
  }): Promise<SupportTicketRow | null> {
    return this.database.withTideTransaction(async (client) => {
      const teacherId = await this.teacherId(client, input.accountId);
      if (!teacherId) return null;
      await this.reconcileOperatorReplies(client, teacherId, input.ticketId);
      await this.insertFiles(client, input.accountId, input.images);
      const result = await client.query<TicketDatabaseRow>(
        `
          SELECT ${ticketSelect}
          FROM public.append_teacher_support_ticket_teacher_message(
            $1, $2, $3, $4::jsonb
          )
        `,
        [
          input.ticketId,
          teacherId,
          input.expectedRowVersion,
          JSON.stringify(input.message),
        ],
      );
      return result.rows[0] ?? null;
    });
  }

  async markRead(
    accountId: string,
    ticketId: string,
  ): Promise<SupportTicketRow | null> {
    return this.database.withTideTransaction(async (client) => {
      const teacherId = await this.teacherId(client, accountId);
      if (!teacherId) return null;
      await this.reconcileOperatorReplies(client, teacherId, ticketId);
      await client.query(
        `
          WITH latest AS (
            SELECT message->>'message_id' AS message_id
            FROM public.teacher_support_tickets source,
                 jsonb_array_elements(source.messages)
                   WITH ORDINALITY AS item(message, position)
            WHERE source.ticket_id = $1
              AND source.teacher_id = $2
              AND message->>'sender' = 'OPERATOR'
            ORDER BY position DESC
            LIMIT 1
          )
          UPDATE public.teacher_support_tickets ticket
          SET
            last_read_operator_message_id = latest.message_id::uuid,
            teacher_last_read_at = now(),
            row_version = row_version + 1,
            updated_at = now()
          FROM latest
          WHERE ticket.ticket_id = $1
            AND ticket.teacher_id = $2
            AND ticket.last_read_operator_message_id IS DISTINCT FROM
                latest.message_id::uuid
        `,
        [ticketId, teacherId],
      );
      return this.findTicket(client, teacherId, ticketId);
    });
  }

  async resolve(
    accountId: string,
    ticketId: string,
    expectedRowVersion: number,
  ): Promise<SupportTicketRow | null> {
    return this.database.withTideTransaction(async (client) => {
      const teacherId = await this.teacherId(client, accountId);
      if (!teacherId) return null;
      await this.reconcileOperatorReplies(client, teacherId, ticketId);
      const result = await client.query<TicketDatabaseRow>(
        `
          UPDATE public.teacher_support_tickets
          SET
            status = 'CLOSED',
            close_reason = 'RESOLVED',
            closed_at = now(),
            teacher_reply_deadline_at = NULL,
            image_cleanup_status = CASE
              WHEN EXISTS (
                SELECT 1
                FROM jsonb_array_elements(messages) message,
                     jsonb_array_elements(
                       COALESCE(message->'images', '[]'::jsonb)
                     ) image
                WHERE image->>'deleted_at' IS NULL
              ) THEN 'PENDING'
              ELSE 'NOT_REQUIRED'
            END,
            row_version = row_version + 1,
            updated_at = now()
          WHERE ticket_id = $1
            AND teacher_id = $2
            AND status <> 'CLOSED'
            AND row_version = $3
          RETURNING ${ticketSelect}
        `,
        [ticketId, teacherId, expectedRowVersion],
      );
      return result.rows[0] ?? null;
    });
  }

  async closeExpired(limit: number): Promise<string[]> {
    return this.database.withTideTransaction(async (client) => {
      const result = await client.query<{ ticketId: string }>(
        `
          WITH due AS (
            SELECT ticket_id
            FROM public.teacher_support_tickets
            WHERE status = 'WAITING_TEACHER'
              AND teacher_reply_deadline_at <= now()
            ORDER BY teacher_reply_deadline_at, ticket_id
            FOR UPDATE SKIP LOCKED
            LIMIT $1
          )
          UPDATE public.teacher_support_tickets ticket
          SET
            status = 'CLOSED',
            close_reason = 'NO_RESPONSE_TIMEOUT',
            closed_at = now(),
            teacher_reply_deadline_at = NULL,
            image_cleanup_status = CASE
              WHEN EXISTS (
                SELECT 1
                FROM jsonb_array_elements(ticket.messages) message,
                     jsonb_array_elements(
                       COALESCE(message->'images', '[]'::jsonb)
                     ) image
                WHERE image->>'deleted_at' IS NULL
              ) THEN 'PENDING'
              ELSE 'NOT_REQUIRED'
            END,
            row_version = row_version + 1,
            updated_at = now()
          FROM due
          WHERE ticket.ticket_id = due.ticket_id
          RETURNING ticket.ticket_id AS "ticketId"
        `,
        [limit],
      );
      return result.rows.map((row) => row.ticketId);
    });
  }

  async cleanupCandidates(limit: number): Promise<CleanupTicketRow[]> {
    const result = await this.database.queryTide<CleanupTicketRow>(
      `
        SELECT ticket_id AS "ticketId", messages
        FROM public.teacher_support_tickets
        WHERE status = 'CLOSED'
          AND image_cleanup_status IN ('PENDING', 'FAILED')
        ORDER BY closed_at, ticket_id
        LIMIT $1
      `,
      [limit],
    );
    return result.rows;
  }

  async markCleanupFailed(ticketId: string): Promise<void> {
    await this.database.queryTide(
      `
        UPDATE public.teacher_support_tickets
        SET image_cleanup_status = 'FAILED', updated_at = now()
        WHERE ticket_id = $1
          AND status = 'CLOSED'
          AND image_cleanup_status IN ('PENDING', 'FAILED')
      `,
      [ticketId],
    );
  }

  async markImagesDeleted(
    ticketId: string,
    objectKeys: string[],
  ): Promise<void> {
    await this.database.withTideTransaction(async (client) => {
      if (objectKeys.length > 0) {
        await client.query(
          `
            UPDATE tide.file_objects
            SET status = 'DELETED', deleted_at = COALESCE(deleted_at, now())
            WHERE object_key = ANY($1::text[])
          `,
          [objectKeys],
        );
      }
      await client.query(
        `
          SELECT public.mark_teacher_support_ticket_images_deleted($1, now())
        `,
        [ticketId],
      );
    });
  }

  async findOwnedImage(
    accountId: string,
    ticketId: string,
    fileId: string,
  ): Promise<OwnedImageRow | null> {
    const result = await this.database.queryTide<OwnedImageRow>(
      `
        SELECT
          image->>'storage_provider' AS "storageProvider",
          image->>'object_key' AS "objectKey",
          image->>'filename' AS filename,
          image->>'mime_type' AS "mimeType"
        FROM public.teacher_support_tickets ticket
        JOIN tide.teacher_bindings binding
          ON binding.teacher_id = ticket.teacher_id
         AND binding.status = 'ACTIVE'
        CROSS JOIN LATERAL jsonb_array_elements(ticket.messages) message
        CROSS JOIN LATERAL jsonb_array_elements(
          COALESCE(message->'images', '[]'::jsonb)
        ) image
        WHERE binding.account_id = $1
          AND ticket.ticket_id = $2
          AND image->>'file_id' = $3
          AND image->>'deleted_at' IS NULL
        LIMIT 1
      `,
      [accountId, ticketId, fileId],
    );
    return result.rows[0] ?? null;
  }

  private async teacherId(
    client: PoolClient,
    accountId: string,
  ): Promise<string | null> {
    const result = await client.query<TeacherBindingRow>(
      `
        SELECT teacher_id AS "teacherId"
        FROM tide.teacher_bindings
        WHERE account_id = $1
          AND status = 'ACTIVE'
        LIMIT 1
      `,
      [accountId],
    );
    return result.rows[0]?.teacherId ?? null;
  }

  private async insertFiles(
    client: PoolClient,
    accountId: string,
    images: StoredSupportImage[],
  ): Promise<void> {
    for (const image of images) {
      await client.query(
        `
          INSERT INTO tide.file_objects (
            id, uploader_account_id, storage_provider, object_key,
            original_filename, mime_type, size_bytes, sha256,
            status, ready_at
          ) VALUES (
            $1, $2, $3, $4, $5, $6, $7, $8, 'READY', now()
          )
        `,
        [
          image.file_id,
          accountId,
          image.storage_provider,
          image.object_key,
          image.filename,
          image.mime_type,
          image.size,
          image.sha256,
        ],
      );
    }
  }

  private async reconcileOperatorReplies(
    client: PoolClient,
    teacherId: string,
    ticketId?: string,
  ): Promise<void> {
    await client.query(
      `
        WITH latest AS (
          SELECT DISTINCT ON (source.ticket_id)
            source.ticket_id,
            (message->>'created_at')::timestamptz AS created_at
          FROM public.teacher_support_tickets source,
               jsonb_array_elements(source.messages)
                 WITH ORDINALITY AS item(message, position)
          WHERE source.teacher_id = $1
            AND ($2::uuid IS NULL OR source.ticket_id = $2::uuid)
            AND source.status <> 'CLOSED'
            AND message->>'sender' = 'OPERATOR'
          ORDER BY source.ticket_id, position DESC
        )
        UPDATE public.teacher_support_tickets ticket
        SET
          status = 'WAITING_TEACHER',
          last_operator_reply_at = latest.created_at,
          teacher_reply_deadline_at = latest.created_at + interval '48 hours',
          row_version = row_version + 1,
          updated_at = now()
        FROM latest
        WHERE ticket.teacher_id = $1
          AND ticket.ticket_id = latest.ticket_id
          AND ($2::uuid IS NULL OR ticket.ticket_id = $2::uuid)
          AND ticket.status <> 'CLOSED'
          AND (
            ticket.last_operator_reply_at IS DISTINCT FROM latest.created_at
            OR ticket.status <> 'WAITING_TEACHER'
          )
          AND NOT EXISTS (
            SELECT 1
            FROM jsonb_array_elements(ticket.messages)
              WITH ORDINALITY AS after_message(message, position)
            WHERE after_message.message->>'sender' = 'TEACHER'
              AND (after_message.message->>'created_at')::timestamptz
                  > latest.created_at
          )
      `,
      [teacherId, ticketId ?? null],
    );
  }

  private async findTicket(
    client: PoolClient,
    teacherId: string,
    ticketId: string,
  ): Promise<SupportTicketRow | null> {
    const result = await client.query<TicketDatabaseRow>(
      `
        SELECT ${ticketSelect}
        FROM public.teacher_support_tickets
        WHERE ticket_id = $1
          AND teacher_id = $2
        LIMIT 1
      `,
      [ticketId, teacherId],
    );
    return result.rows[0] ?? null;
  }
}
