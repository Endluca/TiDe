import type { PoolClient } from 'pg';
import type { FileStorageAdapter } from '../files/file-storage.adapter';
import type { AiGatewayService } from '../integrations/ai/ai-gateway.service';
import { AiImageReviewRuleHandler } from './ai-image-review-rule.handler';
import type { ImageReviewRepository } from './image-review.repository';
import type { TaskRuleContext } from './task-validation.models';

const config = {
  stepKey: 'photo',
  criteriaVersion: 'criteria-v1',
  criteriaKeys: ['clear', 'correct'],
  systemPrompt: 'Return strict JSON.',
  userText: 'Review this evidence.',
};

function createFixture() {
  const saveReview = jest.fn().mockResolvedValue(undefined);
  const storage = {
    read: jest.fn().mockResolvedValue(Buffer.from('image')),
  } as unknown as FileStorageAdapter;
  const gateway = {
    execute: jest.fn(),
  } as unknown as AiGatewayService;
  const reviews = {
    isActivePromptVersion: jest.fn().mockResolvedValue(true),
    findSubmissionFile: jest.fn().mockResolvedValue({
      fileId: '8df36fd5-7ef6-47bb-a73e-72e25623c67f',
      storageProvider: 'OSS',
      objectKey: 'private/evidence.png',
      originalFilename: 'evidence.png',
      mimeType: 'image/png',
    }),
    save: saveReview,
  } as unknown as ImageReviewRepository;
  const handler = new AiImageReviewRuleHandler(storage, gateway, reviews);
  const context: TaskRuleContext = {
    client: {} as PoolClient,
    accountId: 'account-id',
    taskInstanceId: 'task-id',
    submissionId: 'submission-id',
    rule: {
      ruleKey: 'photo-review',
      ruleType: 'AI_IMAGE_REVIEW',
      ruleVersion: '1',
      config,
      teacherFailureCopy: 'retry',
    },
    steps: [],
    outputs: [
      {
        stepKey: 'photo',
        outputType: 'FILE',
        value: { fileId: '8df36fd5-7ef6-47bb-a73e-72e25623c67f' },
      },
    ],
  };
  return { handler, gateway, reviews, saveReview, context };
}

describe('AiImageReviewRuleHandler', () => {
  it('passes only a strictly valid configured result', async () => {
    const fixture = createFixture();
    (fixture.gateway.execute as jest.Mock).mockResolvedValue({
      status: 'SUCCEEDED',
      aiRunId: 'run-id',
      content: JSON.stringify({
        decision: 'PASS',
        teacherReason: '照片符合要求。',
        confidenceSummary: { overall: 0.98 },
        criteria: [
          { criterionKey: 'clear', result: 'PASS', teacherMessage: null },
          { criterionKey: 'correct', result: 'PASS', teacherMessage: null },
        ],
      }),
    });

    await expect(fixture.handler.evaluate(fixture.context)).resolves.toEqual({
      passed: true,
      resultCode: 'IMAGE_REVIEW_PASSED',
      teacherMessage: '照片符合要求。',
    });
    expect(fixture.saveReview).toHaveBeenCalledWith(
      fixture.context.client,
      expect.objectContaining({ decision: 'PASS', aiRunId: 'run-id' }),
    );
  });

  it('maps gateway errors to ERROR and manual review, never teacher failure', async () => {
    const fixture = createFixture();
    (fixture.gateway.execute as jest.Mock).mockResolvedValue({
      status: 'FAILED',
      aiRunId: 'run-id',
      errorCode: 'AI_GATEWAY_UNAVAILABLE',
    });

    await expect(
      fixture.handler.evaluate(fixture.context),
    ).resolves.toMatchObject({
      passed: false,
      deferred: true,
      resultCode: 'AI_GATEWAY_UNAVAILABLE',
    });
    expect(fixture.saveReview).toHaveBeenCalledWith(
      fixture.context.client,
      expect.objectContaining({ decision: 'ERROR' }),
    );
  });

  it('rejects malformed or unexpected criteria as a technical error', async () => {
    const fixture = createFixture();
    (fixture.gateway.execute as jest.Mock).mockResolvedValue({
      status: 'SUCCEEDED',
      aiRunId: 'run-id',
      content: JSON.stringify({
        decision: 'RETRY',
        teacherReason: 'retry',
        confidenceSummary: {},
        criteria: [
          { criterionKey: 'invented', result: 'FAIL', teacherMessage: 'bad' },
        ],
      }),
    });

    await expect(
      fixture.handler.evaluate(fixture.context),
    ).resolves.toMatchObject({
      deferred: true,
      resultCode: 'IMAGE_REVIEW_RESPONSE_INVALID',
    });
  });

  it('never passes when the overall decision conflicts with a failed item', async () => {
    const fixture = createFixture();
    (fixture.gateway.execute as jest.Mock).mockResolvedValue({
      status: 'SUCCEEDED',
      aiRunId: 'run-id',
      content: JSON.stringify({
        decision: 'PASS',
        teacherReason: '请调整画面后重拍。',
        confidenceSummary: { overall: 0.94 },
        criteria: [
          { criterionKey: 'clear', result: 'PASS', teacherMessage: null },
          {
            criterionKey: 'correct',
            result: 'FAIL',
            teacherMessage: '构图不符合要求。',
          },
        ],
      }),
    });

    await expect(fixture.handler.evaluate(fixture.context)).resolves.toEqual({
      passed: false,
      resultCode: 'IMAGE_REVIEW_RETRY',
      teacherMessage: '请调整画面后重拍。',
    });
    expect(fixture.saveReview).toHaveBeenCalledWith(
      fixture.context.client,
      expect.objectContaining({ decision: 'RETRY', aiRunId: 'run-id' }),
    );
  });
});
