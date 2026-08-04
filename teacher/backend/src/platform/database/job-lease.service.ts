import { Injectable } from '@nestjs/common';
import { randomUUID } from 'node:crypto';
import { hostname } from 'node:os';
import { DatabaseService } from './database.service';

export interface JobLeaseResult<Result> {
  acquired: boolean;
  result?: Result;
}

export interface ActiveJobLease {
  readonly signal: AbortSignal;
  assertActive(): void;
}

export class JobLeaseLostError extends Error {
  readonly code = 'BACKGROUND_JOB_LEASE_LOST';

  constructor(
    readonly jobKey: string,
    readonly ownerId: string,
    cause?: unknown,
  ) {
    super(`Background job lease lost: ${jobKey}`);
    this.name = JobLeaseLostError.name;
    this.cause = cause;
  }
}

export function createJobOwner(component: string): string {
  return `${component}:${hostname()}:${process.pid}:${randomUUID()}`;
}

@Injectable()
export class JobLeaseService {
  constructor(private readonly database: DatabaseService) {}

  async runExclusive<Result>(
    jobKey: string,
    ownerId: string,
    leaseMs: number,
    work: (lease: ActiveJobLease) => Promise<Result>,
  ): Promise<JobLeaseResult<Result>> {
    const acquisitionStartedAt = Date.now();
    if (!(await this.tryAcquire(jobKey, ownerId, leaseMs))) {
      return { acquired: false };
    }

    const controller = new AbortController();
    let renewal = Promise.resolve();
    // Start from the request timestamp, not its response timestamp. This is
    // deliberately conservative: a slow DB round trip can only shorten the
    // local validity window, never let this process work past the DB lease.
    let locallyValidUntil = acquisitionStartedAt + leaseMs;
    const loseLease = (cause?: unknown): void => {
      if (!controller.signal.aborted) {
        controller.abort(new JobLeaseLostError(jobKey, ownerId, cause));
      }
    };
    const lease: ActiveJobLease = {
      signal: controller.signal,
      assertActive: () => {
        if (!controller.signal.aborted && Date.now() >= locallyValidUntil) {
          loseLease(new Error('Background job lease deadline elapsed'));
        }
        if (!controller.signal.aborted) return;
        const reason: unknown = controller.signal.reason;
        throw reason instanceof Error
          ? reason
          : new JobLeaseLostError(jobKey, ownerId, reason);
      },
    };
    const renewTimer = setInterval(
      () => {
        renewal = renewal.then(async () => {
          if (controller.signal.aborted) return;
          const renewalStartedAt = Date.now();
          try {
            if (await this.renew(jobKey, ownerId, leaseMs)) {
              locallyValidUntil = renewalStartedAt + leaseMs;
            } else {
              loseLease();
            }
          } catch (error) {
            loseLease(error);
          }
        });
      },
      Math.max(10_000, Math.floor(leaseMs / 3)),
    );
    renewTimer.unref();
    let outcome:
      | { succeeded: true; result: Result }
      | { succeeded: false; error: unknown };
    try {
      const result = await work(lease);
      await renewal;
      lease.assertActive();
      outcome = { succeeded: true, result };
    } catch (error) {
      outcome = { succeeded: false, error };
    } finally {
      clearInterval(renewTimer);
      await renewal;
      try {
        await this.release(jobKey, ownerId);
      } catch (error) {
        // Preserve the work or lease-loss error; expiry still provides takeover.
        if (outcome!.succeeded) {
          outcome = { succeeded: false, error };
        }
      }
    }
    if (!outcome.succeeded) throw outcome.error;
    return { acquired: true, result: outcome.result };
  }

  async tryAcquire(
    jobKey: string,
    ownerId: string,
    leaseMs: number,
  ): Promise<boolean> {
    const result = await this.database.queryTide(
      `
        INSERT INTO tide.job_leases (
          job_key, owner_id, lease_until, updated_at
        ) VALUES (
          $1, $2, now() + ($3 * interval '1 millisecond'), now()
        )
        ON CONFLICT (job_key) DO UPDATE
        SET owner_id = EXCLUDED.owner_id,
            lease_until = EXCLUDED.lease_until,
            updated_at = now()
        WHERE tide.job_leases.lease_until <= now()
           OR tide.job_leases.owner_id = EXCLUDED.owner_id
        RETURNING job_key
      `,
      [jobKey, ownerId, leaseMs],
    );
    return (result.rowCount ?? 0) === 1;
  }

  async renew(
    jobKey: string,
    ownerId: string,
    leaseMs: number,
  ): Promise<boolean> {
    const result = await this.database.queryTide(
      `
        UPDATE tide.job_leases
        SET lease_until = now() + ($3 * interval '1 millisecond'),
            updated_at = now()
        WHERE job_key = $1 AND owner_id = $2
          AND lease_until > now()
      `,
      [jobKey, ownerId, leaseMs],
    );
    return (result.rowCount ?? 0) === 1;
  }

  async release(jobKey: string, ownerId: string): Promise<void> {
    await this.database.queryTide(
      `
        DELETE FROM tide.job_leases
        WHERE job_key = $1 AND owner_id = $2
      `,
      [jobKey, ownerId],
    );
  }
}
