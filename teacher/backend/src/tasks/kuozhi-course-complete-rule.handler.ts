import { Injectable } from '@nestjs/common';
import { TaskRuleHandler } from './task-rule.handler';
import type { TaskRuleContext, TaskRuleResult } from './task-validation.models';

@Injectable()
export class KuozhiCourseCompleteRuleHandler extends TaskRuleHandler {
  readonly ruleType = 'KUOZHI_COURSE_COMPLETE';

  async evaluate(context: TaskRuleContext): Promise<TaskRuleResult> {
    const mappingVersion = Number(context.rule.config.mappingVersion);
    const result = await context.client.query<{ completed: boolean }>(
      `
        SELECT completion_decision AS completed
        FROM tide.kuozhi_course_syncs
        WHERE task_assignment_id = $1
          AND mapping_version = $2
        ORDER BY created_at DESC, id DESC
        LIMIT 1
      `,
      [context.taskInstanceId, mappingVersion],
    );
    const latest = result.rows[0];
    if (!latest) {
      return {
        passed: false,
        deferred: true,
        resultCode: 'KUOZHI_PROGRESS_NOT_SYNCED',
        teacherMessage: '尚未同步到阔知课程进度，请刷新学习进度后重试。',
      };
    }
    return {
      passed: latest.completed,
      resultCode: latest.completed
        ? 'KUOZHI_COURSE_COMPLETE'
        : 'KUOZHI_COURSE_INCOMPLETE',
      teacherMessage: latest.completed ? null : context.rule.teacherFailureCopy,
    };
  }
}
