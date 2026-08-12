import type { PoolClient } from 'pg';
import sharp from 'sharp';
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

let validImage: Buffer;

beforeAll(async () => {
  validImage = await sharp({
    create: {
      width: 640,
      height: 360,
      channels: 3,
      background: { r: 128, g: 128, b: 128 },
    },
  })
    .jpeg()
    .toBuffer();
});

function createFixture() {
  const saveReview = jest
    .fn<
      ReturnType<ImageReviewRepository['save']>,
      Parameters<ImageReviewRepository['save']>
    >()
    .mockResolvedValue(undefined);
  const deleteFile = jest.fn().mockResolvedValue(undefined);
  const storage = {
    read: jest.fn().mockResolvedValue(validImage),
    delete: deleteFile,
  } as unknown as FileStorageAdapter;
  const executeReview = jest.fn<
    ReturnType<AiGatewayService['execute']>,
    Parameters<AiGatewayService['execute']>
  >();
  const hasPassedReview = jest.fn().mockResolvedValue(false);
  const gateway = {
    execute: executeReview,
  } as unknown as AiGatewayService;
  const reviews = {
    isActivePromptVersion: jest.fn().mockResolvedValue(true),
    hasPassedReview,
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
  return {
    handler,
    gateway,
    reviews,
    hasPassedReview,
    saveReview,
    deleteFile,
    executeReview,
    context,
  };
}

describe('AiImageReviewRuleHandler', () => {
  it('passes only a strictly valid configured result', async () => {
    const fixture = createFixture();
    fixture.executeReview.mockResolvedValue({
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
    expect(fixture.deleteFile).not.toHaveBeenCalled();
    const gatewayInput = fixture.executeReview.mock.calls[0][0];
    expect(gatewayInput.systemPrompt).toBe(config.systemPrompt);
    expect(gatewayInput.userText).toBe(config.userText);
    expect(gatewayInput.file).toMatchObject({
      filename: 'evidence.png',
      mimeType: 'image/png',
    });
  });

  it('maps gateway errors to ERROR and manual review, never teacher failure', async () => {
    const fixture = createFixture();
    fixture.executeReview.mockResolvedValue({
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
    fixture.executeReview.mockResolvedValue({
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
    fixture.executeReview.mockResolvedValue({
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

  it('overrides the legacy database rule with the current G04-only policy', async () => {
    const fixture = createFixture();
    fixture.context.rule.config = {
      ...config,
      stepKey: 'g02-environment-photo',
      criteriaVersion: 'g02-environment-2026-07-v2-strict',
      criteriaKeys: [
        'lighting',
        'framing',
        'posture',
        'headset',
        'appearance',
        'background',
        'clarity',
      ],
    };
    fixture.context.outputs[0].stepKey = 'g02-environment-photo';
    fixture.executeReview.mockResolvedValue({
      status: 'SUCCEEDED',
      aiRunId: 'run-id',
      content: JSON.stringify({
        decision: 'PASS',
        teacherReason: '照片符合要求。',
        confidenceSummary: { overall: 0.89 },
        criteria: [
          {
            criterionKey: 'camera_angle',
            result: 'PASS',
            teacherMessage: null,
            confidence: 0.89,
          },
          {
            criterionKey: 'lighting',
            result: 'PASS',
            teacherMessage: null,
            confidence: 0.96,
          },
          {
            criterionKey: 'background',
            result: 'PASS',
            teacherMessage: null,
            confidence: 0.96,
          },
          {
            criterionKey: 'dressing',
            result: 'PASS',
            teacherMessage: null,
            confidence: 0.96,
          },
        ],
      }),
    });

    await expect(fixture.handler.evaluate(fixture.context)).resolves.toEqual({
      passed: false,
      resultCode: 'IMAGE_REVIEW_RETRY',
      teacherMessage: '判断把握不足，请按提示调整后重新拍照。',
    });
    const savedReview = fixture.saveReview.mock.calls[0][1];
    expect(savedReview.decision).toBe('RETRY');
    expect(savedReview.criteriaVersion).toBe(
      'lesson-preparation-camera-view-2026-08-v7-background-veto',
    );
    expect(savedReview.items.map((item) => item.criterionKey)).toEqual([
      'camera_angle',
      'lighting',
      'background',
      'dressing',
    ]);
    const g04GatewayInput = fixture.executeReview.mock.calls[0][0];
    expect(g04GatewayInput.systemPrompt).toContain('camera_angle');
    expect(g04GatewayInput.systemPrompt).toContain('明显侧脸');
    expect(g04GatewayInput.systemPrompt).toContain('头部向左/向右侧倾');
    expect(g04GatewayInput.systemPrompt).toContain('白色虚线辅助轮廓');
    expect(g04GatewayInput.systemPrompt).toContain('背景必须干净、整洁');
    expect(g04GatewayInput.systemPrompt).toContain('画面中只能出现当前老师');
    expect(g04GatewayInput.systemPrompt).toContain('干扰授课的物体');
    expect(g04GatewayInput.systemPrompt).toContain('backgroundChecks');
    expect(g04GatewayInput.systemPrompt).toContain('不得增加第五项');
    expect(g04GatewayInput.userText).toContain('首课准备');
    expect(g04GatewayInput.userText).toContain('背景不干净整洁');
    expect(g04GatewayInput.file).toMatchObject({
      filename: 'evidence-ai.jpg',
      mimeType: 'image/jpeg',
    });
  });

  it('applies the teaching-environment policy through an explicit review profile', async () => {
    const fixture = createFixture();
    fixture.context.rule.config = {
      ...config,
      stepKey: 'p-fb-negative-environment-photo',
      reviewProfile: 'TEACHING_ENVIRONMENT_V1',
    };
    fixture.context.outputs[0].stepKey = 'p-fb-negative-environment-photo';
    fixture.executeReview.mockResolvedValue({
      status: 'SUCCEEDED',
      aiRunId: 'run-id',
      content: JSON.stringify({
        decision: 'PASS',
        teacherReason: '照片符合要求。',
        confidenceSummary: { overall: 0.89 },
        criteria: [
          {
            criterionKey: 'camera_angle',
            result: 'PASS',
            teacherMessage: null,
            confidence: 0.89,
          },
          ...['lighting', 'background', 'dressing'].map((criterionKey) => ({
            criterionKey,
            result: 'PASS',
            teacherMessage: null,
            confidence: 0.96,
          })),
        ],
      }),
    });

    await expect(fixture.handler.evaluate(fixture.context)).resolves.toEqual({
      passed: false,
      resultCode: 'IMAGE_REVIEW_RETRY',
      teacherMessage: '判断把握不足，请按提示调整后重新拍照。',
    });
    expect(fixture.deleteFile).toHaveBeenCalledWith(
      'private/evidence.png',
      'OSS',
    );
    expect(fixture.saveReview).not.toHaveBeenCalled();
    const gatewayInput = fixture.executeReview.mock.calls[0][0];
    expect(gatewayInput.systemPrompt).toBe('Return strict JSON.');
    expect(gatewayInput.userText).toBe('Review this evidence.');
    expect(gatewayInput.file).toMatchObject({
      filename: 'evidence-ai.jpg',
      mimeType: 'image/jpeg',
    });
  });

  it('keeps reusing the first passed review for G04', async () => {
    const fixture = createFixture();
    fixture.context.rule.config = {
      ...config,
      stepKey: 'g02-environment-photo',
    };
    fixture.context.outputs[0].stepKey = 'g02-environment-photo';
    fixture.hasPassedReview.mockResolvedValue(true);

    await expect(fixture.handler.evaluate(fixture.context)).resolves.toEqual({
      passed: true,
      resultCode: 'IMAGE_REVIEW_PASSED',
      teacherMessage: null,
    });
    expect(fixture.executeReview).not.toHaveBeenCalled();
    expect(fixture.saveReview).not.toHaveBeenCalled();
    expect(fixture.deleteFile).not.toHaveBeenCalled();
  });

  it('ignores an earlier PASS and uses only the current personalized photo result', async () => {
    const fixture = createFixture();
    fixture.context.rule.config = {
      ...config,
      stepKey: 'p-fb-negative-environment-photo',
      reviewProfile: 'TEACHING_ENVIRONMENT_V1',
    };
    fixture.context.outputs[0].stepKey = 'p-fb-negative-environment-photo';
    fixture.hasPassedReview.mockResolvedValue(true);
    fixture.executeReview.mockResolvedValue({
      status: 'SUCCEEDED',
      aiRunId: 'current-run-id',
      content: JSON.stringify({
        decision: 'PASS',
        teacherReason: '本次照片符合要求。',
        confidenceSummary: { overall: 0.98 },
        criteria: ['camera_angle', 'lighting', 'background', 'dressing'].map(
          (criterionKey) => ({
            criterionKey,
            result: 'PASS',
            teacherMessage: null,
            confidence: 0.98,
          }),
        ),
      }),
    });

    await expect(fixture.handler.evaluate(fixture.context)).resolves.toEqual({
      passed: true,
      resultCode: 'IMAGE_REVIEW_PASSED',
      teacherMessage: '本次照片符合要求。',
    });
    expect(fixture.hasPassedReview).not.toHaveBeenCalled();
    expect(fixture.executeReview).toHaveBeenCalledTimes(1);
    expect(fixture.deleteFile).toHaveBeenCalledWith(
      'private/evidence.png',
      'OSS',
    );
    expect(fixture.saveReview).not.toHaveBeenCalled();
  });

  it('keeps personalized technical failures retryable without storing a review', async () => {
    const fixture = createFixture();
    fixture.context.rule.config = {
      ...config,
      stepKey: 'p-fb-negative-environment-photo',
      reviewProfile: 'TEACHING_ENVIRONMENT_V1',
    };
    fixture.context.outputs[0].stepKey = 'p-fb-negative-environment-photo';
    fixture.executeReview.mockResolvedValue({
      status: 'FAILED',
      aiRunId: 'current-run-id',
      errorCode: 'AI_GATEWAY_UNAVAILABLE',
    });

    await expect(fixture.handler.evaluate(fixture.context)).resolves.toEqual({
      passed: false,
      resultCode: 'AI_GATEWAY_UNAVAILABLE',
      teacherMessage: '暂时无法完成自动检查，请重新拍照后重试。',
    });
    expect(fixture.deleteFile).toHaveBeenCalledWith(
      'private/evidence.png',
      'OSS',
    );
    expect(fixture.saveReview).not.toHaveBeenCalled();
  });

  it('does not run a personalized review when the temporary photo cannot be deleted', async () => {
    const fixture = createFixture();
    fixture.context.rule.config = {
      ...config,
      stepKey: 'p-fb-negative-environment-photo',
      reviewProfile: 'TEACHING_ENVIRONMENT_V1',
    };
    fixture.context.outputs[0].stepKey = 'p-fb-negative-environment-photo';
    fixture.deleteFile.mockRejectedValue(new Error('delete failed'));

    await expect(fixture.handler.evaluate(fixture.context)).resolves.toEqual({
      passed: false,
      resultCode: 'IMAGE_REVIEW_FILE_DELETE_FAILED',
      teacherMessage: '暂时无法完成自动检查，请重新拍照后重试。',
    });
    expect(fixture.executeReview).not.toHaveBeenCalled();
    expect(fixture.saveReview).not.toHaveBeenCalled();
  });

  it('vetoes a nominal background PASS when the full-frame scan finds another person', async () => {
    const fixture = createFixture();
    fixture.context.rule.config = {
      ...config,
      stepKey: 'g02-environment-photo',
    };
    fixture.context.outputs[0].stepKey = 'g02-environment-photo';
    fixture.executeReview.mockResolvedValue({
      status: 'SUCCEEDED',
      aiRunId: 'run-id',
      content: JSON.stringify({
        decision: 'PASS',
        teacherReason: '照片符合要求。',
        confidenceSummary: {
          backgroundChecks: {
            otherPeopleVisible: true,
            distractingObjectsVisible: true,
            clutterVisible: false,
            sensitiveInformationVisible: false,
            virtualBackgroundDefect: false,
          },
        },
        criteria: ['camera_angle', 'lighting', 'background', 'dressing'].map(
          (criterionKey) => ({
            criterionKey,
            result: 'PASS',
            teacherMessage: null,
            confidence: 0.98,
          }),
        ),
      }),
    });

    await expect(fixture.handler.evaluate(fixture.context)).resolves.toEqual({
      passed: false,
      resultCode: 'IMAGE_REVIEW_RETRY',
      teacherMessage: '背景中出现其他人员，请换到无人入镜的授课区域。',
    });
    const savedReview = fixture.saveReview.mock.calls[0][1];
    expect(savedReview.decision).toBe('RETRY');
    expect(
      savedReview.items.find((item) => item.criterionKey === 'background'),
    ).toMatchObject({
      result: 'FAIL',
      teacherMessage: '背景中出现其他人员，请换到无人入镜的授课区域。',
    });
  });
});
