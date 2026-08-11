import type { PoolClient } from 'pg';
import { G01ExternalStatusRuleHandler } from './g01-external-status-rule.handler';
import type { TaskRuleContext } from './task-validation.models';

function contextWith(tesolCompleted: boolean | null) {
  const query = jest.fn().mockResolvedValue({
    rows: [{ tesolCompleted }],
  });
  return {
    query,
    context: {
      client: { query } as unknown as PoolClient,
      accountId: 'account-id',
      taskInstanceId: 'task-id',
      submissionId: 'submission-id',
      rule: {
        ruleKey: 'g01-external',
        ruleType: 'G01_EXTERNAL_STATUS',
        ruleVersion: '1',
        config: {},
        teacherFailureCopy: 'not ready',
      },
      steps: [],
      outputs: [],
    } satisfies TaskRuleContext,
  };
}

describe('G01ExternalStatusRuleHandler', () => {
  const handler = new G01ExternalStatusRuleHandler();

  it('passes when the real TESOL status is complete without reading Self-intro', async () => {
    const fixture = contextWith(true);
    await expect(handler.evaluate(fixture.context)).resolves.toMatchObject({
      passed: true,
      resultCode: 'G01_EXTERNAL_STATUS_PASSED',
    });
    const calls = fixture.query.mock.calls as unknown as Array<[unknown]>;
    const sql = String(calls[0][0]);
    expect(sql).toContain('public.teacher_source_wide');
    expect(sql).toContain('source.tchr_id = assignment.teacher_id');
    expect(sql).toContain('source.is_cpl_tesol');
    expect(sql).not.toContain('is_self_introduce');
    expect(sql).not.toContain('teacher_metric_snapshots');
  });

  it('does not complete G01 when TESOL is incomplete', async () => {
    const fixture = contextWith(false);
    await expect(handler.evaluate(fixture.context)).resolves.toEqual({
      passed: false,
      resultCode: 'G01_EXTERNAL_STATUS_INCOMPLETE',
      teacherMessage: 'not ready',
    });
  });

  it('defers rather than falsely failing when the real source is unavailable', async () => {
    const query = jest.fn().mockResolvedValue({ rows: [] });
    const fixture = contextWith(null);
    fixture.context.client = { query } as unknown as PoolClient;
    await expect(handler.evaluate(fixture.context)).resolves.toMatchObject({
      passed: false,
      deferred: true,
      resultCode: 'G01_EXTERNAL_STATUS_UNAVAILABLE',
      teacherMessage: '暂时无法读取 TESOL 状态，请稍后重试。',
    });
  });

  it('defers when TESOL is unavailable', async () => {
    const fixture = contextWith(null);
    await expect(handler.evaluate(fixture.context)).resolves.toMatchObject({
      passed: false,
      deferred: true,
      resultCode: 'G01_EXTERNAL_STATUS_UNAVAILABLE',
      teacherMessage: '暂时无法读取 TESOL 状态，请稍后重试。',
    });
  });
});
