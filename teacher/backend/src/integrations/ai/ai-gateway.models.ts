export interface AiGatewayFile {
  content: Buffer;
  filename: string;
  mimeType: string;
}

export interface AiGatewayCompletionInput {
  systemPrompt: string;
  userText: string;
  file?: AiGatewayFile;
}

export interface AiGatewayCompletion {
  content: string;
  gatewayRef: string | null;
  inputUnits: number | null;
  outputUnits: number | null;
}

export type AiGatewayExecutionResult =
  | {
      status: 'SUCCEEDED';
      aiRunId: string;
      content: string;
    }
  | {
      status: 'FAILED';
      aiRunId: string | null;
      errorCode: string;
    };

export class AiGatewayError extends Error {
  constructor(public readonly code: string) {
    super(code);
    this.name = 'AiGatewayError';
  }
}
