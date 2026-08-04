import { Injectable } from '@nestjs/common';
import { TaskRuleHandler } from './task-rule.handler';
import type { TaskRuleContext, TaskRuleResult } from './task-validation.models';

const currentG04CoursewareStepKey = 'g02-courseware-confirmation';
const currentG04LegacyDeviceStepKey = 'g02-device-check';
const currentG04PhotoStepKey = 'g02-environment-photo';

@Injectable()
export class AllStepsCompleteRuleHandler extends TaskRuleHandler {
  readonly ruleType = 'ALL_STEPS_COMPLETE';

  evaluate(context: TaskRuleContext): Promise<TaskRuleResult> {
    const stepKeys = new Set(context.steps.map((step) => step.stepKey));
    const isCurrentG04 =
      stepKeys.has(currentG04CoursewareStepKey) &&
      stepKeys.has(currentG04PhotoStepKey);
    const requiredSteps = isCurrentG04
      ? context.steps.filter(
          (step) => step.stepKey !== currentG04LegacyDeviceStepKey,
        )
      : context.steps;
    const passed =
      requiredSteps.length > 0 &&
      requiredSteps.every(
        (step) => step.status === 'COMPLETED' && step.percent === 100,
      );
    if (passed && context.rule.config.deferAfterPass === true) {
      return Promise.resolve({
        passed: true,
        deferred: true,
        resultCode: 'MANUAL_REVIEW_REQUIRED',
        teacherMessage: null,
      });
    }
    return Promise.resolve({
      passed,
      resultCode: passed ? 'ALL_STEPS_COMPLETE' : 'STEPS_INCOMPLETE',
      teacherMessage: passed
        ? null
        : isCurrentG04
          ? '请完成备课确认和授课环境照片检查。'
          : context.rule.teacherFailureCopy,
    });
  }
}
