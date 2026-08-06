import { Injectable } from '@nestjs/common';
import type { PoolClient } from 'pg';
import { AllStepsCompleteRuleHandler } from './all-steps-complete-rule.handler';
import { AiImageReviewRuleHandler } from './ai-image-review-rule.handler';
import { G01ExternalStatusRuleHandler } from './g01-external-status-rule.handler';
import { KuozhiCourseCompleteRuleHandler } from './kuozhi-course-complete-rule.handler';
import type { StepOutputDto } from './dto/submit-task.dto';
import type { TaskRuleHandler } from './task-rule.handler';
import type {
  TaskValidationDecision,
  TaskValidationRule,
  TaskValidationStep,
} from './task-validation.models';

@Injectable()
export class TaskValidationEngine {
  private readonly handlers: Map<string, TaskRuleHandler>;

  constructor(
    allStepsComplete: AllStepsCompleteRuleHandler,
    aiImageReview: AiImageReviewRuleHandler,
    g01ExternalStatus: G01ExternalStatusRuleHandler,
    kuozhiCourseComplete: KuozhiCourseCompleteRuleHandler,
  ) {
    this.handlers = new Map<string, TaskRuleHandler>([
      [allStepsComplete.ruleType, allStepsComplete],
      [aiImageReview.ruleType, aiImageReview],
      [g01ExternalStatus.ruleType, g01ExternalStatus],
      [kuozhiCourseComplete.ruleType, kuozhiCourseComplete],
    ]);
  }

  async evaluate(input: {
    client: PoolClient;
    accountId: string;
    taskInstanceId: string;
    submissionId: string;
    rules: TaskValidationRule[];
    steps: TaskValidationStep[];
    outputs: StepOutputDto[];
  }): Promise<TaskValidationDecision> {
    if (input.rules.length === 0) {
      return {
        status: 'UNDER_REVIEW',
        resultCode: 'VALIDATION_RULES_NOT_CONFIGURED',
        teacherMessage: null,
        ruleVersion: 'unconfigured',
      };
    }

    const currentG04ImageRule = input.rules.find(
      (rule) =>
        rule.ruleType === 'AI_IMAGE_REVIEW' &&
        rule.config.stepKey === 'g02-environment-photo',
    );
    const rules = currentG04ImageRule
      ? [
          currentG04ImageRule,
          ...input.rules.filter((rule) => rule !== currentG04ImageRule),
        ]
      : input.rules;

    for (const rule of rules) {
      const handler = this.handlers.get(rule.ruleType);
      if (!handler) {
        return {
          status: 'UNDER_REVIEW',
          resultCode: 'VALIDATION_HANDLER_NOT_AVAILABLE',
          teacherMessage: null,
          ruleVersion: `${rule.ruleKey}:${rule.ruleVersion}`,
        };
      }
      const result = await handler.evaluate({
        client: input.client,
        accountId: input.accountId,
        taskInstanceId: input.taskInstanceId,
        submissionId: input.submissionId,
        rule,
        steps: input.steps,
        outputs: input.outputs,
      });
      if (result.deferred) {
        return {
          status: 'UNDER_REVIEW',
          resultCode: result.resultCode,
          teacherMessage: result.teacherMessage,
          ruleVersion: `${rule.ruleKey}:${rule.ruleVersion}`,
        };
      }
      if (!result.passed) {
        return {
          status: 'FAILED',
          resultCode: result.resultCode,
          teacherMessage: result.teacherMessage,
          ruleVersion: `${rule.ruleKey}:${rule.ruleVersion}`,
        };
      }
    }

    return {
      status: 'PASSED',
      resultCode: 'ALL_RULES_PASSED',
      teacherMessage: null,
      ruleVersion: rules
        .map((rule) => `${rule.ruleKey}:${rule.ruleVersion}`)
        .join(','),
    };
  }
}
