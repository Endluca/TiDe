import { AllStepsCompleteRuleHandler } from './all-steps-complete-rule.handler';
import type { PoolClient } from 'pg';
import type { AiImageReviewRuleHandler } from './ai-image-review-rule.handler';
import type { G01ExternalStatusRuleHandler } from './g01-external-status-rule.handler';
import { TaskValidationEngine } from './task-validation.engine';

const rule = {
  ruleKey: 'all-steps',
  ruleType: 'ALL_STEPS_COMPLETE',
  ruleVersion: '1',
  config: {},
  teacherFailureCopy: 'Please finish the remaining step.',
};

describe('TaskValidationEngine', () => {
  const imageReview = {
    ruleType: 'AI_IMAGE_REVIEW',
    evaluate: jest.fn(),
  } as unknown as AiImageReviewRuleHandler;
  const g01ExternalStatus = {
    ruleType: 'G01_EXTERNAL_STATUS',
    evaluate: jest.fn(),
  } as unknown as G01ExternalStatusRuleHandler;
  const engine = new TaskValidationEngine(
    new AllStepsCompleteRuleHandler(),
    imageReview,
    g01ExternalStatus,
  );
  const context = {
    client: {} as PoolClient,
    accountId: 'account-id',
    taskInstanceId: 'task-id',
    submissionId: 'submission-id',
  };

  it('passes only when every configured step is complete', async () => {
    await expect(
      engine.evaluate({
        ...context,
        rules: [rule],
        steps: [{ stepKey: 'read', status: 'COMPLETED', percent: 100 }],
        outputs: [],
      }),
    ).resolves.toMatchObject({
      status: 'PASSED',
      resultCode: 'ALL_RULES_PASSED',
    });
  });

  it('uses Jiahe configured teacher copy when common validation fails', async () => {
    await expect(
      engine.evaluate({
        ...context,
        rules: [rule],
        steps: [{ stepKey: 'read', status: 'IN_PROGRESS', percent: 50 }],
        outputs: [],
      }),
    ).resolves.toEqual({
      status: 'FAILED',
      resultCode: 'STEPS_INCOMPLETE',
      teacherMessage: 'Please finish the remaining step.',
      ruleVersion: 'all-steps:1',
    });
  });

  it('keeps a completed response under review when manual validation is configured', async () => {
    await expect(
      engine.evaluate({
        ...context,
        rules: [{ ...rule, config: { deferAfterPass: true } }],
        steps: [
          { stepKey: 'teacher-response', status: 'COMPLETED', percent: 100 },
        ],
        outputs: [],
      }),
    ).resolves.toEqual({
      status: 'UNDER_REVIEW',
      resultCode: 'MANUAL_REVIEW_REQUIRED',
      teacherMessage: null,
      ruleVersion: 'all-steps:1',
    });
  });

  it('keeps the task under review when a specialized handler is absent', async () => {
    await expect(
      engine.evaluate({
        ...context,
        rules: [{ ...rule, ruleType: 'JIAHE_SPECIAL_RULE' }],
        steps: [],
        outputs: [],
      }),
    ).resolves.toMatchObject({
      status: 'UNDER_REVIEW',
      resultCode: 'VALIDATION_HANDLER_NOT_AVAILABLE',
    });
  });

  it('keeps technical image review failures under review', async () => {
    (imageReview.evaluate as jest.Mock).mockResolvedValueOnce({
      passed: false,
      deferred: true,
      resultCode: 'AI_GATEWAY_UNAVAILABLE',
      teacherMessage: '人工复核',
    });
    await expect(
      engine.evaluate({
        ...context,
        rules: [{ ...rule, ruleType: 'AI_IMAGE_REVIEW' }],
        steps: [],
        outputs: [],
      }),
    ).resolves.toMatchObject({
      status: 'UNDER_REVIEW',
      resultCode: 'AI_GATEWAY_UNAVAILABLE',
    });
  });
});
