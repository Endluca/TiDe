import type { PoolClient } from 'pg';
import { G01ExternalStatusRuleHandler } from './g01-external-status-rule.handler';
import type { TaskRuleContext } from './task-validation.models';

function contextWith(
  selfIntroduced: boolean | null,
  tesolCompleted: boolean | null,
) {
  const query = jest.fn().mockResolvedValue({
    rows: [{ selfIntroduced, tesolCompleted }],
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

  it('passes only when both real statuses are complete', async () => {
    const fixture = contextWith(true, true);
    await expect(handler.evaluate(fixture.context)).resolves.toMatchObject({
      passed: true,
      resultCode: 'G01_EXTERNAL_STATUS_PASSED',
    });
  });

  it('does not complete G01 when either real status is incomplete', async () => {
    const fixture = contextWith(true, false);
    await expect(handler.evaluate(fixture.context)).resolves.toMatchObject({
      passed: false,
      resultCode: 'G01_EXTERNAL_STATUS_INCOMPLETE',
    });
  });

  it('defers rather than falsely failing when the real source is unavailable', async () => {
    const query = jest.fn().mockResolvedValue({ rows: [] });
    const fixture = contextWith(null, null);
    fixture.context.client = { query } as unknown as PoolClient;
    await expect(handler.evaluate(fixture.context)).resolves.toMatchObject({
      passed: false,
      deferred: true,
      resultCode: 'G01_EXTERNAL_STATUS_UNAVAILABLE',
    });
  });
});
