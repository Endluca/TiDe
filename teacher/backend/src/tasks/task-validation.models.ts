import type { StepOutputDto } from './dto/submit-task.dto';
import type { PoolClient } from 'pg';

export interface TaskValidationRule {
  ruleKey: string;
  ruleType: string;
  ruleVersion: string;
  config: Record<string, unknown>;
  teacherFailureCopy: string;
}

export interface TaskValidationStep {
  stepKey: string;
  status: 'NOT_STARTED' | 'IN_PROGRESS' | 'COMPLETED' | 'FAILED';
  percent: number;
}

export interface TaskRuleContext {
  client: PoolClient;
  accountId: string;
  taskInstanceId: string;
  submissionId: string;
  rule: TaskValidationRule;
  steps: TaskValidationStep[];
  outputs: StepOutputDto[];
}

export interface TaskRuleResult {
  passed: boolean;
  deferred?: boolean;
  resultCode: string;
  teacherMessage: string | null;
}

export interface TaskValidationDecision {
  status: 'UNDER_REVIEW' | 'PASSED' | 'FAILED';
  resultCode: string;
  teacherMessage: string | null;
  ruleVersion: string;
}
