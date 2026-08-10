import type { PoolClient } from 'pg';
import { KuozhiCourseCompleteRuleHandler } from './kuozhi-course-complete-rule.handler';
import type { TaskRuleContext } from './task-validation.models';

function contextWith(rows: Array<{ completed: boolean }>) {
  const query = jest.fn().mockResolvedValue({ rows });
  return {
    query,
    context: {
      client: { query } as unknown as PoolClient,
      accountId: 'account-id',
      taskInstanceId: 'assignment-id',
      submissionId: 'submission-id',
      rule: {
        ruleKey: 'g01-kuozhi-course',
        ruleType: 'KUOZHI_COURSE_COMPLETE',
        ruleVersion: '1',
        config: { mappingVersion: 5 },
        teacherFailureCopy: '请先完成阔知考试。',
      },
      steps: [],
      outputs: [],
    } satisfies TaskRuleContext,
  };
}

describe('KuozhiCourseCompleteRuleHandler', () => {
  const handler = new KuozhiCourseCompleteRuleHandler();

  it('passes only when the latest current-mapping sync is complete', async () => {
    const fixture = contextWith([{ completed: true }]);
    await expect(handler.evaluate(fixture.context)).resolves.toEqual({
      passed: true,
      resultCode: 'KUOZHI_COURSE_COMPLETE',
      teacherMessage: null,
    });
    expect(fixture.query).toHaveBeenCalledWith(
      expect.stringContaining('mapping_version = $2'),
      ['assignment-id', 5],
    );
  });

  it('fails while the current course requirements remain incomplete', async () => {
    const fixture = contextWith([{ completed: false }]);
    await expect(handler.evaluate(fixture.context)).resolves.toEqual({
      passed: false,
      resultCode: 'KUOZHI_COURSE_INCOMPLETE',
      teacherMessage: '请先完成阔知考试。',
    });
  });

  it('defers when no current-mapping progress has been synced', async () => {
    const fixture = contextWith([]);
    await expect(handler.evaluate(fixture.context)).resolves.toMatchObject({
      passed: false,
      deferred: true,
      resultCode: 'KUOZHI_PROGRESS_NOT_SYNCED',
    });
  });
});
