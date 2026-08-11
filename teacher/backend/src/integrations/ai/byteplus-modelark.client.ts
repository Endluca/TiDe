import { Injectable, Optional } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import OpenAI from 'openai';
import type {
  ResponseCreateParamsNonStreaming,
  ResponseInputMessageContentList,
} from 'openai/resources/responses/responses';
import type { AppEnvironment } from '../../platform/config/environment';
import { DependencyHealthRegistry } from '../../platform/observability/dependency-health.registry';
import {
  AiGatewayError,
  type AiGatewayCompletion,
  type AiGatewayCompletionInput,
} from './ai-gateway.models';

interface ModelArkConfig {
  apiKey: string;
  baseUrl: string;
  model: string;
  timeoutMs: number;
}

type BytePlusResponseRequest = ResponseCreateParamsNonStreaming & {
  thinking: { type: 'disabled' };
};

@Injectable()
export class BytePlusModelArkClient {
  private client: OpenAI | null = null;

  constructor(
    private readonly config: ConfigService<AppEnvironment, true>,
    @Optional() private readonly dependencyHealth?: DependencyHealthRegistry,
  ) {}

  async complete(
    input: AiGatewayCompletionInput,
  ): Promise<AiGatewayCompletion> {
    const modelArk = this.loadConfig();
    const userContent = this.userContent(input);
    const request: BytePlusResponseRequest = {
      model: modelArk.model,
      input: [
        { role: 'system', content: input.systemPrompt },
        { role: 'user', content: userContent },
      ],
      max_output_tokens: 4_096,
      store: false,
      stream: false,
      temperature: 0,
      text: { format: { type: 'json_object' } },
      thinking: { type: 'disabled' },
    };
    const startedAt = Date.now();
    let response: Awaited<ReturnType<OpenAI['responses']['create']>>;
    try {
      response = await this.sdk(modelArk).responses.create(request);
    } catch (error) {
      const status = this.httpStatus(error);
      this.dependencyHealth?.recordFailure(
        'aiGateway',
        status ? `AI_GATEWAY_HTTP_${status}` : 'AI_GATEWAY_UNAVAILABLE',
        startedAt,
      );
      throw new AiGatewayError(
        status ? 'AI_GATEWAY_HTTP_ERROR' : 'AI_GATEWAY_UNAVAILABLE',
      );
    }

    const content = response.output_text?.trim();
    if (!content) {
      this.dependencyHealth?.recordFailure(
        'aiGateway',
        'AI_GATEWAY_RESPONSE_INVALID',
        startedAt,
      );
      throw new AiGatewayError('AI_GATEWAY_RESPONSE_INVALID');
    }
    this.dependencyHealth?.recordSuccess('aiGateway', startedAt);
    return {
      content,
      gatewayRef: this.stringOrNull(response.id),
      inputUnits: this.numberOrNull(response.usage?.input_tokens),
      outputUnits: this.numberOrNull(response.usage?.output_tokens),
    };
  }

  provider(): string {
    return 'BYTEPLUS_MODELARK';
  }

  model(): string {
    return this.config.get('MODELARK_MODEL', { infer: true });
  }

  private loadConfig(): ModelArkConfig {
    if (!this.config.get('MODELARK_ENABLED', { infer: true })) {
      throw new AiGatewayError('AI_GATEWAY_DISABLED');
    }
    const apiKey = this.config.get('ARK_API_KEY', { infer: true });
    if (!apiKey) {
      throw new AiGatewayError('AI_GATEWAY_NOT_CONFIGURED');
    }
    return {
      apiKey,
      baseUrl: this.config.get('MODELARK_BASE_URL', { infer: true }),
      model: this.model(),
      timeoutMs: this.config.get('MODELARK_TIMEOUT_MS', { infer: true }),
    };
  }

  private sdk(config: ModelArkConfig): OpenAI {
    if (!this.client) {
      this.client = new OpenAI({
        apiKey: config.apiKey,
        baseURL: config.baseUrl,
        maxRetries: 0,
        timeout: config.timeoutMs,
      });
    }
    return this.client;
  }

  private userContent(
    input: AiGatewayCompletionInput,
  ): ResponseInputMessageContentList {
    const content: ResponseInputMessageContentList = [
      { type: 'input_text', text: input.userText },
    ];
    if (!input.file) {
      return content;
    }
    const mimeType = input.file.mimeType.toLowerCase();
    const fileData = `data:${mimeType};base64,${input.file.content.toString('base64')}`;
    if (mimeType.startsWith('image/')) {
      content.push({
        type: 'input_image',
        detail: 'high',
        image_url: fileData,
      });
      return content;
    }
    if (mimeType === 'application/pdf') {
      content.push({
        type: 'input_file',
        file_data: fileData,
        filename: input.file.filename,
      });
      return content;
    }
    throw new AiGatewayError('AI_GATEWAY_FILE_UNSUPPORTED');
  }

  private httpStatus(error: unknown): number | null {
    if (!error || typeof error !== 'object') {
      return null;
    }
    const status = (error as { status?: unknown }).status;
    return typeof status === 'number' && Number.isInteger(status)
      ? status
      : null;
  }

  private stringOrNull(value: unknown): string | null {
    return typeof value === 'string' && value.length > 0 ? value : null;
  }

  private numberOrNull(value: unknown): number | null {
    return typeof value === 'number' && Number.isFinite(value) ? value : null;
  }
}
