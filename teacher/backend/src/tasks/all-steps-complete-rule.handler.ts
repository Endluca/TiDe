import { Injectable } from '@nestjs/common';
import { TaskRuleHandler } from './task-rule.handler';
import type { TaskRuleContext, TaskRuleResult } from './task-validation.models';

const currentG04StepKeys = [
  'g02-environment-photo',
  'g02-courseware-confirmation',
];

@Injectable()
export class AllStepsCompleteRuleHandler extends TaskRuleHandler {
  readonly ruleType = 'ALL_STEPS_COMPLETE';

  evaluate(context: TaskRuleContext): Promise<TaskRuleResult> {
    const configuredRequiredStepKeys = Array.isArray(
      context.rule.config.requiredStepKeys,
    )
      ? [
          ...new Set(
            context.rule.config.requiredStepKeys.filter(
              (value): value is string => typeof value === 'string',
            ),
          ),
        ]
      : [];
    const stepByKey = new Map(
      context.steps.map((step) => [step.stepKey, step]),
    );
    const requiredSteps =
      configuredRequiredStepKeys.length > 0
        ? configuredRequiredStepKeys.map((stepKey) => stepByKey.get(stepKey))
        : context.steps;
    const recognizedStepKeys = new Set(
      configuredRequiredStepKeys.length > 0
        ? configuredRequiredStepKeys
        : context.steps.map((step) => step.stepKey),
    );
    const isCurrentG04 =
      recognizedStepKeys.size === currentG04StepKeys.length &&
      currentG04StepKeys.every((stepKey) => recognizedStepKeys.has(stepKey));
    const passed =
      requiredSteps.length > 0 &&
      requiredSteps.every(
        (step) => step?.status === 'COMPLETED' && step.percent === 100,
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
          ? '请分别完成授课环境照片检查和课件准备确认，两部分可任意顺序完成。'
          : context.rule.teacherFailureCopy,
    });
  }
}
