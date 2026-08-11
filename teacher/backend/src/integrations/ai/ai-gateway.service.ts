import { Injectable } from '@nestjs/common';
import { createHash } from 'node:crypto';
import {
  AiGatewayError,
  type AiGatewayCompletionInput,
  type AiGatewayExecutionResult,
} from './ai-gateway.models';
import { AiRunRepository } from './ai-run.repository';
import { BytePlusModelArkClient } from './byteplus-modelark.client';

@Injectable()
export class AiGatewayService {
  constructor(
    private readonly gateway: BytePlusModelArkClient,
    private readonly runs: AiRunRepository,
  ) {}

  async execute(
    input: AiGatewayCompletionInput & {
      capability: string;
      callerModule: string;
      promptVersionId: string | null;
    },
  ): Promise<AiGatewayExecutionResult> {
    let runId: string | null = null;
    const startedAt = Date.now();
    try {
      runId = await this.runs.start({
        capability: input.capability,
        callerModule: input.callerModule,
        promptVersionId: input.promptVersionId,
        provider: this.gateway.provider(),
        model: this.gateway.model(),
        requestHash: this.requestHash(input),
      });
      const completion = await this.gateway.complete(input);
      await this.runs.succeed({
        runId,
        gatewayRef: completion.gatewayRef,
        latencyMs: Date.now() - startedAt,
        inputUnits: completion.inputUnits,
        outputUnits: completion.outputUnits,
      });
      return {
        status: 'SUCCEEDED',
        aiRunId: runId,
        content: completion.content,
      };
    } catch (error) {
      const errorCode =
        error instanceof AiGatewayError
          ? error.code
          : 'AI_GATEWAY_INTERNAL_ERROR';
      if (runId) {
        await this.runs.fail(runId, errorCode, Date.now() - startedAt);
      }
      return { status: 'FAILED', aiRunId: runId, errorCode };
    }
  }

  private requestHash(input: AiGatewayCompletionInput): string {
    return createHash('sha256')
      .update(
        JSON.stringify({
          systemPrompt: input.systemPrompt,
          userText: input.userText,
          filename: input.file?.filename ?? null,
          mimeType: input.file?.mimeType ?? null,
          fileHash: input.file
            ? createHash('sha256').update(input.file.content).digest('hex')
            : null,
        }),
      )
      .digest('hex');
  }
}
