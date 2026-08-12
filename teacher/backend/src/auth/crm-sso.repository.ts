import { Injectable } from '@nestjs/common';
import { randomUUID } from 'node:crypto';
import type { QueryResultRow } from 'pg';
import { DatabaseService } from '../platform/database/database.service';
import { CrmSsoBindingError } from './crm-sso.models';

interface BoundAccountRow extends QueryResultRow {
  accountId: string;
  normalizedEmail: string;
  accountStatus: string;
  teacherId: string | null;
  bindingStatus: string | null;
}

@Injectable()
export class CrmSsoRepository {
  constructor(private readonly database: DatabaseService) {}

  async createLogin(input: {
    email: string;
    normalizedEmail: string;
    teacherId: string;
    jtiHash: string;
    issuer: string;
    audience: string;
    exchangeCodeHash: string;
    redirectPath: string;
    assertionExpiresAt: Date;
    exchangeExpiresAt: Date;
  }): Promise<{ accountId: string; created: boolean }> {
    try {
      return await this.database.withTideTransaction(async (client) => {
        const result = await client.query<BoundAccountRow>(
          `
            SELECT
              account.id AS "accountId",
              account.normalized_email AS "normalizedEmail",
              account.status AS "accountStatus",
              binding.teacher_id AS "teacherId",
              binding.status AS "bindingStatus"
            FROM tide.user_accounts account
            LEFT JOIN tide.teacher_bindings binding
              ON binding.account_id = account.id
            WHERE account.normalized_email = $1
              OR EXISTS (
                SELECT 1
                FROM tide.teacher_bindings teacher_binding
                WHERE teacher_binding.account_id = account.id
                  AND teacher_binding.teacher_id = $2
              )
            FOR UPDATE OF account
          `,
          [input.normalizedEmail, input.teacherId],
        );
        const emailAccount = result.rows.find(
          (row) => row.normalizedEmail === input.normalizedEmail,
        );
        const teacherAccount = result.rows.find(
          (row) => row.teacherId === input.teacherId,
        );

        let accountId: string;
        let created = false;

        if (teacherAccount) {
          if (
            emailAccount &&
            emailAccount.accountId !== teacherAccount.accountId
          ) {
            throw new CrmSsoBindingError('EMAIL_ALREADY_BOUND');
          }
          if (teacherAccount.normalizedEmail !== input.normalizedEmail) {
            throw new CrmSsoBindingError('TEACHER_ALREADY_BOUND');
          }
          if (teacherAccount.bindingStatus !== 'ACTIVE') {
            throw new CrmSsoBindingError('TEACHER_ALREADY_BOUND');
          }
          if (
            teacherAccount.accountStatus === 'LOCKED' ||
            teacherAccount.accountStatus === 'DISABLED'
          ) {
            throw new CrmSsoBindingError('ACCOUNT_NOT_ACTIVE');
          }

          accountId = teacherAccount.accountId;
          if (teacherAccount.accountStatus === 'PENDING_VERIFICATION') {
            await client.query(
              `
                UPDATE tide.user_accounts
                SET
                  status = 'ACTIVE',
                  email_verified_at = COALESCE(email_verified_at, now()),
                  updated_at = now()
                WHERE id = $1
              `,
              [accountId],
            );
            await client.query(
              `
                UPDATE tide.auth_tokens
                SET revoked_at = now()
                WHERE account_id = $1
                  AND purpose = 'EMAIL_VERIFY'
                  AND used_at IS NULL
                  AND revoked_at IS NULL
              `,
              [accountId],
            );
          }
        } else if (emailAccount) {
          throw new CrmSsoBindingError('EMAIL_ALREADY_BOUND');
        } else {
          accountId = randomUUID();
          const bindingId = randomUUID();
          await client.query(
            `
              INSERT INTO tide.user_accounts (
                id, email, normalized_email, password_hash, status,
                email_verified_at, created_via
              ) VALUES ($1, $2, $3, NULL, 'ACTIVE', now(), 'CRM_SSO')
            `,
            [accountId, input.email, input.normalizedEmail],
          );
          await client.query(
            `
              INSERT INTO tide.teacher_bindings (
                id, account_id, teacher_id
              ) VALUES ($1, $2, $3)
            `,
            [bindingId, accountId, input.teacherId],
          );
          await client.query(
            `
              INSERT INTO tide.binding_audit_events (
                id, binding_id, account_id, event_type, new_teacher_id,
                actor_type, reason
              ) VALUES (
                $1, $2, $3, 'BOUND', $4, 'SYSTEM', 'CRM_SSO_FIRST_LOGIN'
              )
            `,
            [randomUUID(), bindingId, accountId, input.teacherId],
          );
          created = true;
        }

        await client.query(
          `
            INSERT INTO tide.crm_sso_logins (
              id, jti_hash, issuer, audience, account_id,
              exchange_code_hash, redirect_path, assertion_expires_at,
              exchange_expires_at
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
          `,
          [
            randomUUID(),
            input.jtiHash,
            input.issuer,
            input.audience,
            accountId,
            input.exchangeCodeHash,
            input.redirectPath,
            input.assertionExpiresAt,
            input.exchangeExpiresAt,
          ],
        );

        return { accountId, created };
      });
    } catch (error) {
      if (error instanceof CrmSsoBindingError) throw error;

      const postgresError = error as { code?: string; constraint?: string };
      if (
        postgresError.code === '23505' &&
        postgresError.constraint === 'crm_sso_logins_jti_hash_key'
      ) {
        throw new CrmSsoBindingError('CRM_SSO_REPLAYED');
      }
      if (postgresError.code === '23505') {
        if (postgresError.constraint === 'user_accounts_normalized_email_key') {
          throw new CrmSsoBindingError('EMAIL_ALREADY_BOUND');
        }
        if (postgresError.constraint === 'teacher_bindings_teacher_key') {
          throw new CrmSsoBindingError('TEACHER_ALREADY_BOUND');
        }
      }

      throw error;
    }
  }
}
