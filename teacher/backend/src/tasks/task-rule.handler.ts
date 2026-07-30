import type { TaskRuleContext, TaskRuleResult } from './task-validation.models';

export abstract class TaskRuleHandler {
  abstract readonly ruleType: string;
  abstract evaluate(context: TaskRuleContext): Promise<TaskRuleResult>;
}
