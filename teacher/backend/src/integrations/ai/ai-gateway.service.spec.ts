import { AiGatewayService } from './ai-gateway.service';
import { AiGatewayError } from './ai-gateway.models';
import type { AiRunRepository } from './ai-run.repository';
import type { CompanyAiGatewayClient } from './company-ai-gateway.client';

function createFixture() {
  let recordedStartInput: Parameters<AiRunRepository['start']>[0] | undefined;
  const complete = jest.fn();
  const gateway = {
    provider: jest.fn().mockReturnValue('VERTEX'),
    model: jest.fn().mockReturnValue('doubao-seed-2-0-lite'),
    complete,
  } as unknown as CompanyAiGatewayClient;
  const start = jest
    .fn<
      ReturnType<AiRunRepository['start']>,
      Parameters<AiRunRepository['start']>
    >()
    .mockImplementation((input: Parameters<AiRunRepository['start']>[0]) => {
      recordedStartInput = input;
      return Promise.resolve('run-id');
    });
  const succeed = jest
    .fn<
      ReturnType<AiRunRepository['succeed']>,
      Parameters<AiRunRepository['succeed']>
    >()
    .mockResolvedValue(undefined);
  const fail = jest
    .fn<
      ReturnType<AiRunRepository['fail']>,
      Parameters<AiRunRepository['fail']>
    >()
    .mockResolvedValue(undefined);
  const runs = { start, succeed, fail } as unknown as AiRunRepository;
  return {
    service: new AiGatewayService(gateway, runs),
    complete,
    start,
    succeed,
    fail,
    getRecordedStartInput: () => recordedStartInput,
  };
}

const request = {
  capability: 'TASK_IMAGE_REVIEW',
  callerModule: 'TASK_VALIDATION',
  promptVersionId: null,
  systemPrompt: 'private prompt',
  userText: 'evidence',
};

describe('AiGatewayService', () => {
  it('records a successful call without storing raw request content', async () => {
    const fixture = createFixture();
    fixture.complete.mockResolvedValue({
      content: 'strict result',
      gatewayRef: 'gateway-ref',
      inputUnits: 2,
      outputUnits: 1,
    });

    await expect(fixture.service.execute(request)).resolves.toEqual({
      status: 'SUCCEEDED',
      aiRunId: 'run-id',
      content: 'strict result',
    });
    expect(fixture.start).toHaveBeenCalledTimes(1);
    const startInput = fixture.getRecordedStartInput();
    expect(startInput).toBeDefined();
    if (!startInput) {
      throw new Error('AI run audit was not started');
    }
    expect(startInput.requestHash).toMatch(/^[a-f0-9]{64}$/);
    expect(JSON.stringify(startInput)).not.toContain('private prompt');
    expect(fixture.succeed).toHaveBeenCalled();
  });

  it('records a stable error code and returns a safe failure result', async () => {
    const fixture = createFixture();
    fixture.complete.mockRejectedValue(
      new AiGatewayError('AI_GATEWAY_UNAVAILABLE'),
    );

    await expect(fixture.service.execute(request)).resolves.toEqual({
      status: 'FAILED',
      aiRunId: 'run-id',
      errorCode: 'AI_GATEWAY_UNAVAILABLE',
    });
    expect(fixture.fail).toHaveBeenCalledWith(
      'run-id',
      'AI_GATEWAY_UNAVAILABLE',
      expect.any(Number),
    );
  });
});
