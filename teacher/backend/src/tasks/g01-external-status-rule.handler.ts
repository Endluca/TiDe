import { Injectable } from '@nestjs/common';
import { TaskRuleHandler } from './task-rule.handler';
import type { TaskRuleContext, TaskRuleResult } from './task-validation.models';

@Injectable()
export class G01ExternalStatusRuleHandler extends TaskRuleHandler {
  readonly ruleType = 'G01_EXTERNAL_STATUS';

  async evaluate(context: TaskRuleContext): Promise<TaskRuleResult> {
    const result = await context.client.query<{
      selfIntroduced: boolean | null;
      tesolCompleted: boolean | null;
    }>(
      `
        SELECT
          snapshot.is_self_introduce AS "selfIntroduced",
          snapshot.is_cpl_tesol AS "tesolCompleted"
        FROM public.task_assignments assignment
        LEFT JOIN LATERAL (
          SELECT metric.is_self_introduce, metric.is_cpl_tesol
          FROM public.teacher_metric_snapshots metric
          WHERE metric.teacher_id = assignment.teacher_id
          ORDER BY metric.updated_at DESC, metric.created_at DESC,
            metric.snapshot_id DESC
          LIMIT 1
        ) snapshot ON true
        WHERE assignment.assignment_id = $1
          AND assignment.task_code = 'G01'
        LIMIT 1
      `,
      [context.taskInstanceId],
    );
    const evidence = result.rows[0];
    if (!evidence) {
      return {
        passed: false,
        deferred: true,
        resultCode: 'G01_EXTERNAL_STATUS_UNAVAILABLE',
        teacherMessage: '暂时无法读取 Self-intro 和 TESOL 状态，请稍后重试。',
      };
    }
    if (evidence.selfIntroduced === true && evidence.tesolCompleted === true) {
      return {
        passed: true,
        resultCode: 'G01_EXTERNAL_STATUS_PASSED',
        teacherMessage: null,
      };
    }
    return {
      passed: false,
      resultCode: 'G01_EXTERNAL_STATUS_INCOMPLETE',
      teacherMessage:
        'Self-intro 和 TESOL 真实状态尚未全部通过，你仍可查看相关学习资料。',
    };
  }
}
