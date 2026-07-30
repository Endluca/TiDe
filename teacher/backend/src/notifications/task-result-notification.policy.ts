export type TaskResultNotificationStatus = 'UNDER_REVIEW' | 'PASSED' | 'FAILED';

export interface TaskResultNotificationContent {
  typeCode:
    'TASK_REVIEW_PENDING' | 'TASK_REVIEW_COMPLETED' | 'TASK_RETRY_REQUIRED';
  title: string;
  body: string;
}

export function buildTaskResultNotificationContent(
  taskTitle: string,
  taskCode: string,
  status: TaskResultNotificationStatus,
): TaskResultNotificationContent {
  const safeTitle =
    taskTitle.trim().length > 0 && !/[\u3400-\u9fff]/u.test(taskTitle)
      ? taskTitle.trim()
      : taskCode;

  const content: Record<
    TaskResultNotificationStatus,
    TaskResultNotificationContent
  > = {
    PASSED: {
      typeCode: 'TASK_REVIEW_COMPLETED',
      title: `Nice work — ${safeTitle} is complete`,
      body: 'Your submission has been reviewed and marked complete. You can continue with your next task in My TIDE.',
    },
    FAILED: {
      typeCode: 'TASK_RETRY_REQUIRED',
      title: `${safeTitle} is ready for another try`,
      body: 'Your submission has been reviewed. Open the task to see what to adjust, then try again when you’re ready.',
    },
    UNDER_REVIEW: {
      typeCode: 'TASK_REVIEW_PENDING',
      title: `We’re reviewing ${safeTitle}`,
      body: 'Your work has been saved and is being reviewed. We’ll let you know when the result is ready.',
    },
  };
  return content[status];
}
