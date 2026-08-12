import { Injectable } from '@nestjs/common';
import { TaskRuleHandler } from './task-rule.handler';
import type { TaskRuleContext, TaskRuleResult } from './task-validation.models';

@Injectable()
export class G01ExternalStatusRuleHandler extends TaskRuleHandler {
  readonly ruleType = 'G01_EXTERNAL_STATUS';

  async evaluate(context: TaskRuleContext): Promise<TaskRuleResult> {
    const result = await context.client.query<{
      tesolCompleted: boolean | null;
    }>(
      `
        SELECT
          source.is_cpl_tesol AS "tesolCompleted"
        FROM public.task_assignments assignment
        JOIN public.teacher_g01_status_current source
          ON source.tchr_id = assignment.teacher_id
        WHERE assignment.assignment_id = $1
          AND assignment.task_code = 'G01'
        LIMIT 1
      `,
      [context.taskInstanceId],
    );
    const evidence = result.rows[0];
    if (!evidence || evidence.tesolCompleted === null) {
      return {
        passed: false,
        deferred: true,
        resultCode: 'G01_EXTERNAL_STATUS_UNAVAILABLE',
        teacherMessage: '暂时无法读取 TESOL 状态，请稍后重试。',
      };
    }
    if (evidence.tesolCompleted === true) {
      return {
        passed: true,
        resultCode: 'G01_EXTERNAL_STATUS_PASSED',
        teacherMessage: null,
      };
    }
    return {
      passed: false,
      resultCode: 'G01_EXTERNAL_STATUS_INCOMPLETE',
      teacherMessage: context.rule.teacherFailureCopy,
    };
  }
}
