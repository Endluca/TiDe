import { Injectable } from '@nestjs/common';
import { TaskRuleHandler } from './task-rule.handler';
import type { TaskRuleContext, TaskRuleResult } from './task-validation.models';

@Injectable()
export class AllStepsCompleteRuleHandler extends TaskRuleHandler {
  readonly ruleType = 'ALL_STEPS_COMPLETE';

  evaluate(context: TaskRuleContext): Promise<TaskRuleResult> {
    const passed =
      context.steps.length > 0 &&
      context.steps.every(
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
      teacherMessage: passed ? null : context.rule.teacherFailureCopy,
    });
  }
}
