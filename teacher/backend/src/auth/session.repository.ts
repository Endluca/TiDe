import { Injectable } from '@nestjs/common';
import { randomUUID } from 'node:crypto';
import type { QueryResultRow } from 'pg';
import { DatabaseService } from '../platform/database/database.service';

export interface LoginAccount {
  accountId: string;
  passwordHash: string | null;
  status: string;
}

export interface RefreshSession {
  sessionId: string;
  accountId: string;
  accountStatus: string;
  authMethod: 'PASSWORD' | 'CRM_SSO';
  expiresAt: Date;
}

export type SessionAuthMethod = 'PASSWORD' | 'CRM_SSO';

export class CrmSsoSessionAccountInactiveError extends Error {
  constructor() {
    super('CRM_SSO_ACCOUNT_NOT_ACTIVE');
    this.name = 'CrmSsoSessionAccountInactiveError';
  }
}

interface LoginAccountRow extends QueryResultRow, LoginAccount {}
interface RefreshSessionRow extends QueryResultRow, RefreshSession {}

@Injectable()
export class SessionRepository {
  constructor(private readonly database: DatabaseService) {}

  async findLoginAccount(
    normalizedEmail: string,
  ): Promise<LoginAccount | null> {
    const result = await this.database.queryTide<LoginAccountRow>(
      `
        SELECT
          id AS "accountId",
          password_hash AS "passwordHash",
          status
        FROM tide.user_accounts
        WHERE normalized_email = $1
        LIMIT 1
      `,
      [normalizedEmail],
    );

    return result.rows[0] ?? null;
  }

  createSession(input: {
    sessionId: string;
    accountId: string;
    refreshTokenHash: string;
    deviceSummary: string | null;
    ipHash: string | null;
    expiresAt: Date;
    authMethod: SessionAuthMethod;
  }): Promise<unknown> {
    return this.database.queryTide(
      `
        INSERT INTO tide.auth_sessions (
          id, account_id, refresh_token_hash, device_summary, ip_hash,
          expires_at, auth_method
        ) VALUES ($1, $2, $3, $4, $5, $6, $7)
      `,
      [
        input.sessionId,
        input.accountId,
        input.refreshTokenHash,
        input.deviceSummary,
        input.ipHash,
        input.expiresAt,
        input.authMethod,
      ],
    );
  }

  async createSessionFromCrmExchange(input: {
    exchangeCodeHash: string;
    sessionId: string;
    refreshTokenHash: string;
    deviceSummary: string | null;
    ipHash: string | null;
    expiresAt: Date;
  }): Promise<{ accountId: string; redirectPath: string } | null> {
    return this.database.withTideTransaction(async (client) => {
      const exchange = await client.query<
        QueryResultRow & { accountId: string; redirectPath: string }
      >(
        `
          UPDATE tide.crm_sso_logins
          SET exchanged_at = now()
          WHERE exchange_code_hash = $1
            AND exchanged_at IS NULL
            AND exchange_expires_at > now()
          RETURNING
            account_id AS "accountId",
            redirect_path AS "redirectPath"
        `,
        [input.exchangeCodeHash],
      );
      const consumed = exchange.rows[0];

      if (!consumed) return null;

      const inserted = await client.query(
        `
          INSERT INTO tide.auth_sessions (
            id, account_id, refresh_token_hash, device_summary, ip_hash,
            expires_at, auth_method
          )
          SELECT $2, account.id, $3, $4, $5, $6, 'CRM_SSO'
          FROM tide.user_accounts account
          WHERE account.id = $1
            AND account.status = 'ACTIVE'
        `,
        [
          consumed.accountId,
          input.sessionId,
          input.refreshTokenHash,
          input.deviceSummary,
          input.ipHash,
          input.expiresAt,
        ],
      );

      if (inserted.rowCount !== 1) {
        throw new CrmSsoSessionAccountInactiveError();
      }

      return consumed;
    });
  }

  async findByRefreshTokenHash(
    refreshTokenHash: string,
  ): Promise<RefreshSession | null> {
    const result = await this.database.queryTide<RefreshSessionRow>(
      `
        SELECT
          session.id AS "sessionId",
          session.account_id AS "accountId",
          account.status AS "accountStatus",
          session.auth_method AS "authMethod",
          session.expires_at AS "expiresAt"
        FROM tide.auth_sessions session
        JOIN tide.user_accounts account ON account.id = session.account_id
        WHERE session.refresh_token_hash = $1
          AND session.revoked_at IS NULL
          AND session.expires_at > now()
        LIMIT 1
      `,
      [refreshTokenHash],
    );

    return result.rows[0] ?? null;
  }

  async rotateRefreshToken(input: {
    sessionId: string;
    previousHash: string;
    nextHash: string;
    deviceSummary: string | null;
    ipHash: string | null;
  }): Promise<boolean> {
    const result = await this.database.queryTide(
      `
        UPDATE tide.auth_sessions
        SET
          refresh_token_hash = $3,
          device_summary = COALESCE($4, device_summary),
          ip_hash = COALESCE($5, ip_hash),
          last_seen_at = now()
        WHERE id = $1
          AND refresh_token_hash = $2
          AND revoked_at IS NULL
          AND expires_at > now()
      `,
      [
        input.sessionId,
        input.previousHash,
        input.nextHash,
        input.deviceSummary,
        input.ipHash,
      ],
    );

    return result.rowCount === 1;
  }

  async isActive(
    sessionId: string,
    accountId: string,
    requiredAuthMethod: SessionAuthMethod | null = null,
  ): Promise<boolean> {
    const result = await this.database.queryTide(
      `
        SELECT 1
        FROM tide.auth_sessions session
        JOIN tide.user_accounts account ON account.id = session.account_id
        WHERE session.id = $1
          AND session.account_id = $2
          AND session.revoked_at IS NULL
          AND session.expires_at > now()
          AND account.status = 'ACTIVE'
          AND ($3::varchar IS NULL OR session.auth_method = $3)
      `,
      [sessionId, accountId, requiredAuthMethod],
    );

    return result.rowCount === 1;
  }

  revoke(sessionId: string, accountId: string): Promise<unknown> {
    return this.database.queryTide(
      `
        UPDATE tide.auth_sessions
        SET revoked_at = COALESCE(revoked_at, now())
        WHERE id = $1 AND account_id = $2
      `,
      [sessionId, accountId],
    );
  }

  recordSecurityEvent(input: {
    accountId: string | null;
    eventType: string;
    outcome: 'SUCCESS' | 'FAILURE' | 'BLOCKED';
    ipHash: string | null;
    deviceSummary: string | null;
    reasonCode: string | null;
  }): Promise<unknown> {
    return this.database.queryTide(
      `
        INSERT INTO tide.auth_security_events (
          id, account_id, event_type, outcome,
          ip_hash, device_summary, reason_code
        ) VALUES ($1, $2, $3, $4, $5, $6, $7)
      `,
      [
        randomUUID(),
        input.accountId,
        input.eventType,
        input.outcome,
        input.ipHash,
        input.deviceSummary,
        input.reasonCode,
      ],
    );
  }
}
