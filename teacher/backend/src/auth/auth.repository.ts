import { Injectable } from '@nestjs/common';
import { randomUUID } from 'node:crypto';
import type { PoolClient, QueryResultRow } from 'pg';
import { DatabaseService } from '../platform/database/database.service';
import type {
  ConsumeVerificationResult,
  RegistrationRecord,
} from './auth.models';

export interface CreateRegistrationInput {
  accountId: string;
  bindingId: string;
  bindingAuditId: string;
  tokenId: string;
  deliveryId: string;
  email: string;
  normalizedEmail: string;
  passwordHash: string;
  teacherId: string;
  tokenHash: string;
  tokenExpiresAt: Date;
  recipientEmailHash: string;
  templateId: string;
}

export interface CreateVerificationChallengeInput {
  accountId: string;
  tokenId: string;
  deliveryId: string;
  tokenHash: string;
  tokenExpiresAt: Date;
  recipientEmailHash: string;
  templateId: string;
}

export class RegistrationConflictError extends Error {
  constructor(
    public readonly reason: 'EMAIL_ALREADY_BOUND' | 'TEACHER_ALREADY_BOUND',
  ) {
    super(reason);
    this.name = 'RegistrationConflictError';
  }
}

interface PendingAccountRow extends QueryResultRow {
  accountId: string;
  email: string;
  accountStatus: string;
}

interface VerificationTokenRow extends QueryResultRow {
  tokenId: string;
  accountId: string;
  expiresAt: Date;
  usedAt: Date | null;
  revokedAt: Date | null;
  accountStatus: string;
}

@Injectable()
export class AuthRepository {
  constructor(private readonly database: DatabaseService) {}

  async createRegistration(
    input: CreateRegistrationInput,
  ): Promise<RegistrationRecord> {
    try {
      return await this.database.withTideTransaction(async (client) => {
        await client.query(
          `
            INSERT INTO tide.user_accounts (
              id, email, normalized_email, password_hash
            ) VALUES ($1, $2, $3, $4)
          `,
          [
            input.accountId,
            input.email,
            input.normalizedEmail,
            input.passwordHash,
          ],
        );
        await client.query(
          `
            INSERT INTO tide.teacher_bindings (
              id, account_id, teacher_id
            ) VALUES ($1, $2, $3)
          `,
          [input.bindingId, input.accountId, input.teacherId],
        );
        await client.query(
          `
            INSERT INTO tide.binding_audit_events (
              id, binding_id, account_id, event_type,
              new_teacher_id, actor_type, reason
            ) VALUES ($1, $2, $3, 'BOUND', $4, 'SYSTEM', 'SELF_REGISTRATION')
          `,
          [
            input.bindingAuditId,
            input.bindingId,
            input.accountId,
            input.teacherId,
          ],
        );
        await this.insertVerificationChallenge(client, input);

        return {
          accountId: input.accountId,
          accountStatus: 'PENDING_VERIFICATION',
        };
      });
    } catch (error) {
      throw this.mapRegistrationConflict(error);
    }
  }

  async findPendingAccountByEmail(
    normalizedEmail: string,
  ): Promise<PendingAccountRow | null> {
    const result = await this.database.queryTide<PendingAccountRow>(
      `
        SELECT
          id AS "accountId",
          email,
          status AS "accountStatus"
        FROM tide.user_accounts
        WHERE normalized_email = $1
        LIMIT 1
      `,
      [normalizedEmail],
    );

    return result.rows[0] ?? null;
  }

  createVerificationChallenge(
    input: CreateVerificationChallengeInput,
  ): Promise<void> {
    return this.database.withTideTransaction(async (client) => {
      await client.query(
        `
          UPDATE tide.auth_tokens
          SET revoked_at = now()
          WHERE account_id = $1
            AND purpose = 'EMAIL_VERIFY'
            AND used_at IS NULL
            AND revoked_at IS NULL
        `,
        [input.accountId],
      );
      await this.insertVerificationChallenge(client, input);
    });
  }

  async consumeVerificationToken(
    tokenHash: string,
  ): Promise<ConsumeVerificationResult> {
    return this.database.withTideTransaction(async (client) => {
      const result = await client.query<VerificationTokenRow>(
        `
          SELECT
            token.id AS "tokenId",
            token.account_id AS "accountId",
            token.expires_at AS "expiresAt",
            token.used_at AS "usedAt",
            token.revoked_at AS "revokedAt",
            account.status AS "accountStatus"
          FROM tide.auth_tokens token
          JOIN tide.user_accounts account ON account.id = token.account_id
          WHERE token.token_hash = $1
            AND token.purpose = 'EMAIL_VERIFY'
          FOR UPDATE OF token, account
        `,
        [tokenHash],
      );
      const token = result.rows[0];

      if (!token) {
        return 'INVALID_OR_EXPIRED';
      }

      if (token.usedAt && token.accountStatus === 'ACTIVE') {
        return 'ALREADY_VERIFIED';
      }

      if (
        token.usedAt ||
        token.revokedAt ||
        token.expiresAt.getTime() <= Date.now()
      ) {
        return 'INVALID_OR_EXPIRED';
      }

      await client.query(
        `
          UPDATE tide.auth_tokens
          SET used_at = now()
          WHERE id = $1
        `,
        [token.tokenId],
      );
      await client.query(
        `
          UPDATE tide.user_accounts
          SET status = 'ACTIVE', email_verified_at = now(), updated_at = now()
          WHERE id = $1
        `,
        [token.accountId],
      );
      await client.query(
        `
          UPDATE tide.auth_tokens
          SET revoked_at = now()
          WHERE account_id = $1
            AND purpose = 'EMAIL_VERIFY'
            AND id <> $2
            AND used_at IS NULL
            AND revoked_at IS NULL
        `,
        [token.accountId, token.tokenId],
      );
      await client.query(
        `
          INSERT INTO tide.auth_security_events (
            id, account_id, event_type, outcome
          ) VALUES ($1, $2, 'EMAIL_VERIFICATION', 'SUCCESS')
        `,
        [randomUUID(), token.accountId],
      );

      return 'VERIFIED';
    });
  }

  markEmailDelivery(
    deliveryId: string,
    status: 'SENT' | 'FAILED',
    providerMessageId: string | null,
    errorCode: string | null,
  ): Promise<unknown> {
    return this.database.queryTide(
      `
        UPDATE tide.email_deliveries
        SET
          status = $2,
          attempt_count = attempt_count + 1,
          provider_message_id = $3,
          last_error_code = $4,
          delivered_at = CASE WHEN $2 = 'SENT' THEN now() ELSE delivered_at END,
          updated_at = now()
        WHERE id = $1
      `,
      [deliveryId, status, providerMessageId, errorCode],
    );
  }

  private async insertVerificationChallenge(
    client: PoolClient,
    input: CreateVerificationChallengeInput,
  ): Promise<void> {
    await client.query(
      `
        INSERT INTO tide.auth_tokens (
          id, account_id, purpose, token_hash, expires_at
        ) VALUES ($1, $2, 'EMAIL_VERIFY', $3, $4)
      `,
      [input.tokenId, input.accountId, input.tokenHash, input.tokenExpiresAt],
    );
    await client.query(
      `
        INSERT INTO tide.email_deliveries (
          id, account_id, purpose, template_id, recipient_email_hash
        ) VALUES ($1, $2, 'EMAIL_VERIFY', $3, $4)
      `,
      [
        input.deliveryId,
        input.accountId,
        input.templateId,
        input.recipientEmailHash,
      ],
    );
  }

  private mapRegistrationConflict(error: unknown): unknown {
    const postgresError = error as { code?: string; constraint?: string };

    if (postgresError.code !== '23505') {
      return error;
    }

    if (postgresError.constraint === 'user_accounts_normalized_email_key') {
      return new RegistrationConflictError('EMAIL_ALREADY_BOUND');
    }

    if (postgresError.constraint === 'teacher_bindings_teacher_key') {
      return new RegistrationConflictError('TEACHER_ALREADY_BOUND');
    }

    return error;
  }
}
