import { Injectable } from '@nestjs/common';
import type { PoolClient, QueryResultRow } from 'pg';
import { DatabaseService } from '../platform/database/database.service';
import type {
  OnboardingAcknowledgementOutcome,
  OnboardingStatus,
} from './onboarding.models';

interface OnboardingStateRow extends QueryResultRow {
  guideCode: string;
  guideVersion: number;
  status: OnboardingStatus;
  idempotencyKey: string;
  requestHash: string | null;
  acknowledgedAt: Date;
}

export interface OnboardingStateRecord {
  guideCode: string;
  guideVersion: number;
  status: OnboardingStatus;
  acknowledgedAt: Date;
}

interface AcknowledgeInput {
  accountId: string;
  guideCode: string;
  guideVersion: number;
  outcome: OnboardingAcknowledgementOutcome;
  idempotencyKey: string;
  requestHash: string;
}

export class OnboardingIdempotencyConflictError extends Error {}

@Injectable()
export class OnboardingRepository {
  constructor(private readonly database: DatabaseService) {}

  async findState(
    accountId: string,
    guideCode: string,
    guideVersion: number,
  ): Promise<OnboardingStateRecord | null> {
    const result = await this.database.queryTide<OnboardingStateRow>(
      `
        SELECT guide_code AS "guideCode", guide_version AS "guideVersion",
          status, idempotency_key AS "idempotencyKey",
          request_hash AS "requestHash", acknowledged_at AS "acknowledgedAt"
        FROM tide.account_onboarding_states
        WHERE account_id = $1 AND guide_code = $2 AND guide_version = $3
        LIMIT 1
      `,
      [accountId, guideCode, guideVersion],
    );
    return result.rows[0] ? this.toRecord(result.rows[0]) : null;
  }

  async findStates(accountId: string): Promise<OnboardingStateRecord[]> {
    const result = await this.database.queryTide<OnboardingStateRow>(
      `
        SELECT guide_code AS "guideCode", guide_version AS "guideVersion",
          status, idempotency_key AS "idempotencyKey",
          request_hash AS "requestHash", acknowledged_at AS "acknowledgedAt"
        FROM tide.account_onboarding_states
        WHERE account_id = $1
      `,
      [accountId],
    );
    return result.rows.map((row) => this.toRecord(row));
  }

  acknowledge(input: AcknowledgeInput): Promise<OnboardingStateRecord> {
    return this.database.withTideTransaction(async (client) => {
      await client.query(
        `SELECT pg_advisory_xact_lock(hashtextextended($1, 0))`,
        [`onboarding:${input.accountId}`],
      );

      const keyed = await this.findByIdempotencyKey(
        client,
        input.accountId,
        input.idempotencyKey,
      );
      if (keyed) {
        if (
          keyed.guideCode !== input.guideCode ||
          Number(keyed.guideVersion) !== input.guideVersion ||
          keyed.requestHash !== input.requestHash
        ) {
          throw new OnboardingIdempotencyConflictError();
        }
        return this.toRecord(keyed);
      }

      const existing = await this.findTargetState(client, input);
      if (existing) return this.toRecord(existing);

      const inserted = await client.query<OnboardingStateRow>(
        `
          INSERT INTO tide.account_onboarding_states (
            account_id, guide_code, guide_version, status,
            idempotency_key, request_hash, acknowledged_at
          ) VALUES ($1, $2, $3, $4, $5, $6, now())
          RETURNING guide_code AS "guideCode", guide_version AS "guideVersion",
            status, idempotency_key AS "idempotencyKey",
            request_hash AS "requestHash", acknowledged_at AS "acknowledgedAt"
        `,
        [
          input.accountId,
          input.guideCode,
          input.guideVersion,
          input.outcome,
          input.idempotencyKey,
          input.requestHash,
        ],
      );
      return this.toRecord(inserted.rows[0]);
    });
  }

  private async findByIdempotencyKey(
    client: PoolClient,
    accountId: string,
    idempotencyKey: string,
  ): Promise<OnboardingStateRow | null> {
    const result = await client.query<OnboardingStateRow>(
      `
        SELECT guide_code AS "guideCode", guide_version AS "guideVersion",
          status, idempotency_key AS "idempotencyKey",
          request_hash AS "requestHash", acknowledged_at AS "acknowledgedAt"
        FROM tide.account_onboarding_states
        WHERE account_id = $1 AND idempotency_key = $2
        LIMIT 1
      `,
      [accountId, idempotencyKey],
    );
    return result.rows[0] ?? null;
  }

  private async findTargetState(
    client: PoolClient,
    input: AcknowledgeInput,
  ): Promise<OnboardingStateRow | null> {
    const result = await client.query<OnboardingStateRow>(
      `
        SELECT guide_code AS "guideCode", guide_version AS "guideVersion",
          status, idempotency_key AS "idempotencyKey",
          request_hash AS "requestHash", acknowledged_at AS "acknowledgedAt"
        FROM tide.account_onboarding_states
        WHERE account_id = $1 AND guide_code = $2 AND guide_version = $3
        LIMIT 1
      `,
      [input.accountId, input.guideCode, input.guideVersion],
    );
    return result.rows[0] ?? null;
  }

  private toRecord(row: OnboardingStateRow): OnboardingStateRecord {
    return {
      guideCode: row.guideCode,
      guideVersion: Number(row.guideVersion),
      status: row.status,
      acknowledgedAt: row.acknowledgedAt,
    };
  }
}
