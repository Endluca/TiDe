import { Injectable } from '@nestjs/common';
import { randomUUID } from 'node:crypto';
import { hostname } from 'node:os';
import { DatabaseService } from './database.service';

export interface JobLeaseResult<Result> {
  acquired: boolean;
  result?: Result;
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
    work: () => Promise<Result>,
  ): Promise<JobLeaseResult<Result>> {
    if (!(await this.tryAcquire(jobKey, ownerId, leaseMs))) {
      return { acquired: false };
    }

    const renewTimer = setInterval(
      () => void this.renew(jobKey, ownerId, leaseMs),
      Math.max(10_000, Math.floor(leaseMs / 3)),
    );
    renewTimer.unref();
    try {
      return { acquired: true, result: await work() };
    } finally {
      clearInterval(renewTimer);
      await this.release(jobKey, ownerId);
    }
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
