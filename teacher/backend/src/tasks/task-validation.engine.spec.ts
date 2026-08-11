import { AllStepsCompleteRuleHandler } from './all-steps-complete-rule.handler';
import type { PoolClient } from 'pg';
import type { AiImageReviewRuleHandler } from './ai-image-review-rule.handler';
import type { G01ExternalStatusRuleHandler } from './g01-external-status-rule.handler';
import type { KuozhiCourseCompleteRuleHandler } from './kuozhi-course-complete-rule.handler';
import { TaskValidationEngine } from './task-validation.engine';

const rule = {
  ruleKey: 'all-steps',
  ruleType: 'ALL_STEPS_COMPLETE',
  ruleVersion: '1',
  config: {},
  teacherFailureCopy: 'Please finish the remaining step.',
};

const g04Rule = {
  ...rule,
  config: {
    requiredStepKeys: ['g02-environment-photo', 'g02-courseware-confirmation'],
  },
};

describe('TaskValidationEngine', () => {
  const evaluateImage = jest.fn();
  const imageReview = {
    ruleType: 'AI_IMAGE_REVIEW',
    evaluate: evaluateImage,
  } as unknown as AiImageReviewRuleHandler;
  const g01ExternalStatus = {
    ruleType: 'G01_EXTERNAL_STATUS',
    evaluate: jest.fn(),
  } as unknown as G01ExternalStatusRuleHandler;
  const kuozhiCourseComplete = {
    ruleType: 'KUOZHI_COURSE_COMPLETE',
    evaluate: jest.fn(),
  } as unknown as KuozhiCourseCompleteRuleHandler;
  const engine = new TaskValidationEngine(
    new AllStepsCompleteRuleHandler(),
    imageReview,
    g01ExternalStatus,
    kuozhiCourseComplete,
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

  it('requires the courseware step for the current two-part G04 flow', async () => {
    await expect(
      engine.evaluate({
        ...context,
        rules: [g04Rule],
        steps: [
          {
            stepKey: 'g02-environment-photo',
            status: 'COMPLETED',
            percent: 100,
          },
          {
            stepKey: 'g02-courseware-confirmation',
            status: 'NOT_STARTED',
            percent: 0,
          },
        ],
        outputs: [],
      }),
    ).resolves.toEqual({
      status: 'FAILED',
      resultCode: 'STEPS_INCOMPLETE',
      teacherMessage:
        '请分别完成授课环境照片检查和课件准备确认，两部分可任意顺序完成。',
      ruleVersion: 'all-steps:1',
    });
  });

  it('keeps the configured failure copy for the legacy three-part G04 rule', async () => {
    const legacyFailureCopy = '请完成备课、设备检测和授课环境照片。';

    await expect(
      engine.evaluate({
        ...context,
        rules: [
          {
            ...rule,
            config: {
              requiredStepKeys: [
                'g02-device-check',
                'g02-environment-photo',
                'g02-courseware-confirmation',
              ],
            },
            teacherFailureCopy: legacyFailureCopy,
          },
        ],
        steps: [
          {
            stepKey: 'g02-device-check',
            status: 'NOT_STARTED',
            percent: 0,
          },
          {
            stepKey: 'g02-environment-photo',
            status: 'COMPLETED',
            percent: 100,
          },
          {
            stepKey: 'g02-courseware-confirmation',
            status: 'COMPLETED',
            percent: 100,
          },
        ],
        outputs: [],
      }),
    ).resolves.toMatchObject({
      status: 'FAILED',
      resultCode: 'STEPS_INCOMPLETE',
      teacherMessage: legacyFailureCopy,
    });
  });

  it('fails closed when the G04 execution catalog is missing a required part', async () => {
    await expect(
      engine.evaluate({
        ...context,
        rules: [g04Rule],
        steps: [
          {
            stepKey: 'g02-environment-photo',
            status: 'COMPLETED',
            percent: 100,
          },
        ],
        outputs: [],
      }),
    ).resolves.toMatchObject({
      status: 'FAILED',
      resultCode: 'STEPS_INCOMPLETE',
    });
  });

  it('passes G04 only after both independent parts reach 100%', async () => {
    await expect(
      engine.evaluate({
        ...context,
        rules: [g04Rule],
        steps: [
          {
            stepKey: 'g02-environment-photo',
            status: 'COMPLETED',
            percent: 100,
          },
          {
            stepKey: 'g02-courseware-confirmation',
            status: 'COMPLETED',
            percent: 100,
          },
        ],
        outputs: [],
      }),
    ).resolves.toMatchObject({
      status: 'PASSED',
      resultCode: 'ALL_RULES_PASSED',
    });
  });

  it('reviews the G04 photo before checking the separate preparation confirmation', async () => {
    evaluateImage.mockResolvedValueOnce({
      passed: true,
      resultCode: 'IMAGE_REVIEW_PASSED',
      teacherMessage: null,
    });
    const imageRule = {
      ...rule,
      ruleKey: 'g04-image-review',
      ruleType: 'AI_IMAGE_REVIEW',
      config: { stepKey: 'g02-environment-photo' },
    };

    await expect(
      engine.evaluate({
        ...context,
        rules: [g04Rule, imageRule],
        steps: [
          {
            stepKey: 'g02-courseware-confirmation',
            status: 'NOT_STARTED',
            percent: 0,
          },
          {
            stepKey: 'g02-environment-photo',
            status: 'COMPLETED',
            percent: 100,
          },
        ],
        outputs: [],
      }),
    ).resolves.toMatchObject({
      status: 'FAILED',
      resultCode: 'STEPS_INCOMPLETE',
    });
    expect(evaluateImage).toHaveBeenCalled();
  });

  it('still requires every step outside the current G04 flow', async () => {
    await expect(
      engine.evaluate({
        ...context,
        rules: [rule],
        steps: [
          { stepKey: 'read', status: 'COMPLETED', percent: 100 },
          {
            stepKey: 'device-check',
            status: 'NOT_STARTED',
            percent: 0,
          },
        ],
        outputs: [],
      }),
    ).resolves.toMatchObject({
      status: 'FAILED',
      resultCode: 'STEPS_INCOMPLETE',
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
    evaluateImage.mockResolvedValueOnce({
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
