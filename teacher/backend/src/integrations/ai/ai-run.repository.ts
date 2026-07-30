import { Injectable } from '@nestjs/common';
import { randomUUID } from 'node:crypto';
import { DatabaseService } from '../../platform/database/database.service';

@Injectable()
export class AiRunRepository {
  constructor(private readonly database: DatabaseService) {}

  async start(input: {
    capability: string;
    callerModule: string;
    promptVersionId: string | null;
    provider: string;
    model: string;
    requestHash: string;
  }): Promise<string> {
    const runId = randomUUID();
    await this.database.queryTide(
      `
        INSERT INTO tide.ai_runs (
          id, capability, caller_module, prompt_version_id,
          provider, model, gateway_ref, request_hash, status
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'RUNNING')
      `,
      [
        runId,
        input.capability,
        input.callerModule,
        input.promptVersionId,
        input.provider,
        input.model,
        `local:${runId}`,
        input.requestHash,
      ],
    );
    return runId;
  }

  async succeed(input: {
    runId: string;
    gatewayRef: string | null;
    latencyMs: number;
    inputUnits: number | null;
    outputUnits: number | null;
  }): Promise<void> {
    await this.database.queryTide(
      `
        UPDATE tide.ai_runs
        SET status = 'SUCCEEDED', gateway_ref = COALESCE($2, gateway_ref),
            latency_ms = $3, input_units = $4, output_units = $5,
            finished_at = now()
        WHERE id = $1
      `,
      [
        input.runId,
        input.gatewayRef,
        input.latencyMs,
        input.inputUnits,
        input.outputUnits,
      ],
    );
  }

  async fail(
    runId: string,
    errorCode: string,
    latencyMs: number,
  ): Promise<void> {
    await this.database.queryTide(
      `
        UPDATE tide.ai_runs
        SET status = 'FAILED', error_code = $2, latency_ms = $3,
            finished_at = now()
        WHERE id = $1
      `,
      [runId, errorCode, latencyMs],
    );
  }
}
