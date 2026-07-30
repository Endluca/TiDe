import { Injectable } from '@nestjs/common';
import { randomUUID } from 'node:crypto';
import type { PoolClient, QueryResultRow } from 'pg';
import { DatabaseService } from '../platform/database/database.service';

export interface PasswordResetAccount {
  accountId: string;
  email: string;
  status: string;
}

interface PasswordResetAccountRow
  extends QueryResultRow, PasswordResetAccount {}

interface PasswordResetTokenRow extends QueryResultRow {
  tokenId: string;
  accountId: string;
  expiresAt: Date;
  usedAt: Date | null;
  revokedAt: Date | null;
  accountStatus: string;
}

@Injectable()
export class PasswordResetRepository {
  constructor(private readonly database: DatabaseService) {}

  async findAccount(
    normalizedEmail: string,
  ): Promise<PasswordResetAccount | null> {
    const result = await this.database.queryTide<PasswordResetAccountRow>(
      `
        SELECT id AS "accountId", email, status
        FROM tide.user_accounts
        WHERE normalized_email = $1
        LIMIT 1
      `,
      [normalizedEmail],
    );
    return result.rows[0] ?? null;
  }

  createChallenge(input: {
    accountId: string;
    tokenId: string;
    deliveryId: string;
    tokenHash: string;
    tokenExpiresAt: Date;
    recipientEmailHash: string;
    templateId: string;
  }): Promise<void> {
    return this.database.withTideTransaction(async (client) => {
      await client.query(
        `
          UPDATE tide.auth_tokens
          SET revoked_at = now()
          WHERE account_id = $1
            AND purpose = 'PASSWORD_RESET'
            AND used_at IS NULL
            AND revoked_at IS NULL
        `,
        [input.accountId],
      );
      await client.query(
        `
          INSERT INTO tide.auth_tokens (
            id, account_id, purpose, token_hash, expires_at
          ) VALUES ($1, $2, 'PASSWORD_RESET', $3, $4)
        `,
        [input.tokenId, input.accountId, input.tokenHash, input.tokenExpiresAt],
      );
      await client.query(
        `
          INSERT INTO tide.email_deliveries (
            id, account_id, purpose, template_id, recipient_email_hash
          ) VALUES ($1, $2, 'PASSWORD_RESET', $3, $4)
        `,
        [
          input.deliveryId,
          input.accountId,
          input.templateId,
          input.recipientEmailHash,
        ],
      );
    });
  }

  async consumeToken(
    tokenHash: string,
    nextPasswordHash: string,
  ): Promise<boolean> {
    return this.database.withTideTransaction(async (client) => {
      const token = await this.lockToken(client, tokenHash);

      if (
        !token ||
        token.accountStatus !== 'ACTIVE' ||
        token.usedAt ||
        token.revokedAt ||
        token.expiresAt.getTime() <= Date.now()
      ) {
        return false;
      }

      await client.query(
        'UPDATE tide.auth_tokens SET used_at = now() WHERE id = $1',
        [token.tokenId],
      );
      await client.query(
        `
          UPDATE tide.user_accounts
          SET password_hash = $2, updated_at = now()
          WHERE id = $1
        `,
        [token.accountId, nextPasswordHash],
      );
      await client.query(
        `
          UPDATE tide.auth_sessions
          SET revoked_at = COALESCE(revoked_at, now())
          WHERE account_id = $1
        `,
        [token.accountId],
      );
      await client.query(
        `
          UPDATE tide.auth_tokens
          SET revoked_at = now()
          WHERE account_id = $1
            AND purpose = 'PASSWORD_RESET'
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
          ) VALUES ($1, $2, 'PASSWORD_RESET', 'SUCCESS')
        `,
        [randomUUID(), token.accountId],
      );
      await client.query(
        `
          INSERT INTO tide.system_notifications (
            system_notification_id, teacher_id, type_code, title, body,
            dedupe_key, payload, issued_at
          )
          SELECT
            gen_random_uuid(), binding.teacher_id,
            'ACCOUNT_SECURITY_PASSWORD_CHANGED',
            'Your password was changed',
            'Your TIDE password was changed successfully. If this wasn’t you, please contact support as soon as possible.',
            'account-security:password-reset:' || $2::text || ':' || binding.teacher_id,
            jsonb_build_object('securityEvent', 'PASSWORD_RESET'),
            now()
          FROM tide.teacher_bindings binding
          WHERE binding.account_id = $1
            AND binding.status = 'ACTIVE'
          ON CONFLICT (dedupe_key) DO NOTHING
        `,
        [token.accountId, token.tokenId],
      );
      return true;
    });
  }

  markDelivery(
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

  private async lockToken(
    client: PoolClient,
    tokenHash: string,
  ): Promise<PasswordResetTokenRow | null> {
    const result = await client.query<PasswordResetTokenRow>(
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
          AND token.purpose = 'PASSWORD_RESET'
        FOR UPDATE OF token, account
      `,
      [tokenHash],
    );
    return result.rows[0] ?? null;
  }
}
