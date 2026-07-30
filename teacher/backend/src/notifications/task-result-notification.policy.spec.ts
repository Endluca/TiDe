import { buildTaskResultNotificationContent } from './task-result-notification.policy';

describe('task result notification policy', () => {
  it('uses encouraging completion copy', () => {
    expect(
      buildTaskResultNotificationContent('Platform Policies', 'G02', 'PASSED'),
    ).toEqual({
      typeCode: 'TASK_REVIEW_COMPLETED',
      title: 'Nice work — Platform Policies is complete',
      body: 'Your submission has been reviewed and marked complete. You can continue with your next task in My TIDE.',
    });
  });

  it('gives a clear next step when another attempt is needed', () => {
    expect(
      buildTaskResultNotificationContent('Platform Policies', 'G02', 'FAILED'),
    ).toEqual({
      typeCode: 'TASK_RETRY_REQUIRED',
      title: 'Platform Policies is ready for another try',
      body: 'Your submission has been reviewed. Open the task to see what to adjust, then try again when you’re ready.',
    });
  });

  it('uses the task code instead of a Chinese or empty title', () => {
    expect(
      buildTaskResultNotificationContent('平台规则', 'G02', 'UNDER_REVIEW'),
    ).toEqual({
      typeCode: 'TASK_REVIEW_PENDING',
      title: 'We’re reviewing G02',
      body: 'Your work has been saved and is being reviewed. We’ll let you know when the result is ready.',
    });
  });
});
