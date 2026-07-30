import { Injectable, Optional } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import type { AppEnvironment } from '../../platform/config/environment';
import { DependencyHealthRegistry } from '../../platform/observability/dependency-health.registry';
import {
  AiGatewayError,
  type AiGatewayCompletion,
  type AiGatewayCompletionInput,
} from './ai-gateway.models';

interface GatewayConfig {
  apiKey: string;
  bizId: string;
  bizType: string;
  chatUrl: string;
  model: string;
  provider: string;
  timeoutMs: number;
  uploadBucket: string;
  uploadProvider: string;
  uploadUrl: string;
}

@Injectable()
export class CompanyAiGatewayClient {
  constructor(
    private readonly config: ConfigService<AppEnvironment, true>,
    @Optional() private readonly dependencyHealth?: DependencyHealthRegistry,
  ) {}

  async complete(
    input: AiGatewayCompletionInput,
  ): Promise<AiGatewayCompletion> {
    const gateway = this.loadConfig();
    const fileUrl = input.file
      ? input.file.mimeType.toLowerCase().startsWith('image/')
        ? this.imageDataUrl(input.file)
        : await this.uploadFile(input.file, gateway)
      : undefined;
    const userContent = fileUrl
      ? [
          {
            type: 'file_url',
            text: input.userText,
            file_url: { url: fileUrl },
          },
        ]
      : [{ type: 'text', text: input.userText }];
    const response = await this.requestJson(gateway.chatUrl, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({
        provider: gateway.provider,
        api_key: gateway.apiKey,
        stream: false,
        presence_penalty: 1,
        biz_type: gateway.bizType,
        temperature: 0,
        messages: [
          {
            role: 'system',
            content: [{ type: 'text', text: input.systemPrompt }],
          },
          { role: 'user', content: userContent },
        ],
        model: gateway.model,
        biz_id: gateway.bizId,
        n: 1,
      }),
      signal: AbortSignal.timeout(gateway.timeoutMs),
    });
    const root = this.asRecord(response);
    if (root.success !== true) {
      throw new AiGatewayError('AI_GATEWAY_REJECTED');
    }
    const result = this.asRecord(root.res);
    const choices = result.choices;
    if (!Array.isArray(choices) || choices.length === 0) {
      throw new AiGatewayError('AI_GATEWAY_RESPONSE_INVALID');
    }
    const message = this.asRecord(this.asRecord(choices[0]).message);
    if (typeof message.content !== 'string' || message.content.length === 0) {
      throw new AiGatewayError('AI_GATEWAY_RESPONSE_INVALID');
    }
    const usage = this.asRecord(result.usage);
    return {
      content: message.content,
      gatewayRef: this.stringOrNull(result.id ?? root.request_id),
      inputUnits: this.numberOrNull(usage.prompt_tokens),
      outputUnits: this.numberOrNull(usage.completion_tokens),
    };
  }

  provider(): string {
    return this.config.get('AI_GATEWAY_PROVIDER', { infer: true });
  }

  model(): string {
    return this.config.get('AI_GATEWAY_MODEL', { infer: true });
  }

  private loadConfig(): GatewayConfig {
    if (!this.config.get('AI_GATEWAY_ENABLED', { infer: true })) {
      throw new AiGatewayError('AI_GATEWAY_DISABLED');
    }
    const apiKey = this.config.get('AI_GATEWAY_API_KEY', { infer: true });
    if (!apiKey) {
      throw new AiGatewayError('AI_GATEWAY_NOT_CONFIGURED');
    }
    return {
      apiKey,
      bizId: this.config.get('AI_GATEWAY_BIZ_ID', { infer: true }),
      bizType: this.config.get('AI_GATEWAY_BIZ_TYPE', { infer: true }),
      chatUrl: this.config.get('AI_GATEWAY_CHAT_URL', { infer: true }),
      model: this.model(),
      provider: this.provider(),
      timeoutMs: this.config.get('AI_GATEWAY_TIMEOUT_MS', { infer: true }),
      uploadBucket: this.config.get('AI_GATEWAY_UPLOAD_BUCKET', {
        infer: true,
      }),
      uploadProvider: this.config.get('AI_GATEWAY_UPLOAD_PROVIDER', {
        infer: true,
      }),
      uploadUrl: this.config.get('AI_GATEWAY_UPLOAD_URL', { infer: true }),
    };
  }

  private async uploadFile(
    file: NonNullable<AiGatewayCompletionInput['file']>,
    gateway: GatewayConfig,
  ): Promise<string> {
    const form = new FormData();
    form.set(
      'file',
      new Blob([Uint8Array.from(file.content)], { type: file.mimeType }),
      file.filename,
    );
    form.set('provider', gateway.uploadProvider);
    form.set('file_name', file.filename);
    form.set('bucket_name', gateway.uploadBucket);
    const response = await this.requestJson(gateway.uploadUrl, {
      method: 'POST',
      body: form,
      signal: AbortSignal.timeout(gateway.timeoutMs),
    });
    const root = this.asRecord(response);
    const result = this.asRecord(root.res);
    if (root.success !== true || typeof result.file_url !== 'string') {
      throw new AiGatewayError('AI_GATEWAY_UPLOAD_FAILED');
    }
    return result.file_url;
  }

  private imageDataUrl(
    file: NonNullable<AiGatewayCompletionInput['file']>,
  ): string {
    return `data:${file.mimeType.toLowerCase()};base64,${file.content.toString('base64')}`;
  }

  private async requestJson(url: string, init: RequestInit): Promise<unknown> {
    const startedAt = Date.now();
    let response: Response;
    try {
      response = await fetch(url, init);
    } catch {
      this.dependencyHealth?.recordFailure(
        'aiGateway',
        'AI_GATEWAY_UNAVAILABLE',
        startedAt,
      );
      throw new AiGatewayError('AI_GATEWAY_UNAVAILABLE');
    }
    if (!response.ok) {
      this.dependencyHealth?.recordFailure(
        'aiGateway',
        `AI_GATEWAY_HTTP_${response.status}`,
        startedAt,
      );
      throw new AiGatewayError('AI_GATEWAY_HTTP_ERROR');
    }
    try {
      const payload = (await response.json()) as unknown;
      this.dependencyHealth?.recordSuccess('aiGateway', startedAt);
      return payload;
    } catch {
      this.dependencyHealth?.recordFailure(
        'aiGateway',
        'AI_GATEWAY_RESPONSE_INVALID',
        startedAt,
      );
      throw new AiGatewayError('AI_GATEWAY_RESPONSE_INVALID');
    }
  }

  private asRecord(value: unknown): Record<string, unknown> {
    return value !== null && typeof value === 'object' && !Array.isArray(value)
      ? (value as Record<string, unknown>)
      : {};
  }

  private stringOrNull(value: unknown): string | null {
    return typeof value === 'string' && value.length > 0 ? value : null;
  }

  private numberOrNull(value: unknown): number | null {
    return typeof value === 'number' && Number.isFinite(value) ? value : null;
  }
}
