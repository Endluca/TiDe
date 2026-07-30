export const PERSONALIZED_TASK_REMINDER_STATUSES = [
  'ASSIGNED',
  'VIEWED',
  'IN_PROGRESS',
  'FAILED',
] as const;

export type PersonalizedTaskReminderStatus =
  (typeof PERSONALIZED_TASK_REMINDER_STATUSES)[number];

export interface PersonalizedTaskReminderAssignment {
  assignmentId: string;
  teacherId: string;
  taskCode: string;
  displayTitle: string | null;
  status: string;
  assignedAt: Date;
  dueAt: Date | null;
  timezoneUsed: string | null;
  timezoneSource: string | null;
  timezoneVerifiedAt: Date | null;
}

export interface PersonalizedTaskReminder {
  teacherId: string;
  typeCode: 'PERSONALIZED_TASK_ASSIGNED' | 'PERSONALIZED_TASK_DUE_24H';
  title: string;
  body: string;
  actionType: 'TASK_DETAIL';
  actionTarget: string;
  expiresAt: Date | null;
  dedupeKey: string;
  assignmentId: string;
  reminderType: 'ASSIGNED' | 'DUE_24H';
}

const MAX_TITLE_LENGTH = 160;
const UNSAFE_TITLE_PATTERN =
  /(?:evidence|internal|complaint|blacklist(?:ed)?|high[-_\s]?risk|risk[-_\s]?(?:level|label)|red[-_\s]?flag|error[-_\s]?code|投诉|拉黑|高危|风险标签|内部证据|错误码|扣分|淘汰|红线)/iu;
const CONTROL_CHARACTER_PATTERN = /\p{Cc}/u;
const DUE_WINDOW_MILLISECONDS = 24 * 60 * 60 * 1000;
const ASSIGNED_TITLE_SUFFIX = ' is ready for you';
const DUE_TITLE_SUFFIX = ' is due soon';

export function personalizedTaskReminderTitle(
  displayTitle: string | null,
  taskCode: string,
): string {
  const candidate = displayTitle?.trim() ?? '';
  if (
    candidate.length === 0 ||
    candidate.length > MAX_TITLE_LENGTH ||
    CONTROL_CHARACTER_PATTERN.test(candidate) ||
    UNSAFE_TITLE_PATTERN.test(candidate)
  ) {
    return taskCode;
  }
  return candidate;
}

function notificationTitle(
  taskTitle: string,
  taskCode: string,
  suffix: string,
): string {
  const title = `${taskTitle}${suffix}`;
  return title.length <= MAX_TITLE_LENGTH ? title : `${taskCode}${suffix}`;
}

function formatDueAt(dueAt: Date, timezoneUsed: string | null): string {
  const options: Intl.DateTimeFormatOptions = {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
    hour12: true,
    timeZoneName: 'short',
    timeZone: timezoneUsed ?? 'UTC',
  };
  try {
    return new Intl.DateTimeFormat('en-US', options).format(dueAt);
  } catch {
    return new Intl.DateTimeFormat('en-US', {
      ...options,
      timeZone: 'UTC',
    }).format(dueAt);
  }
}

export function buildPersonalizedTaskReminders(
  assignment: PersonalizedTaskReminderAssignment,
  now: Date,
  rolloutAt: Date,
): PersonalizedTaskReminder[] {
  const reminders: PersonalizedTaskReminder[] = [];
  const title = personalizedTaskReminderTitle(
    assignment.displayTitle,
    assignment.taskCode,
  );
  const actionTarget = `/task/${assignment.assignmentId}`;

  if (
    assignment.assignedAt.getTime() >= rolloutAt.getTime() &&
    assignment.assignedAt.getTime() <= now.getTime()
  ) {
    reminders.push({
      teacherId: assignment.teacherId,
      typeCode: 'PERSONALIZED_TASK_ASSIGNED',
      title: notificationTitle(
        title,
        assignment.taskCode,
        ASSIGNED_TITLE_SUFFIX,
      ),
      body: 'This improvement task is ready in My TIDE. It is designed to help you strengthen one part of your teaching. Start whenever you’re ready.',
      actionType: 'TASK_DETAIL',
      actionTarget,
      expiresAt: assignment.dueAt,
      dedupeKey: `personalized-task-assigned:${assignment.assignmentId}`,
      assignmentId: assignment.assignmentId,
      reminderType: 'ASSIGNED',
    });
  }

  const dueAt = assignment.dueAt?.getTime();
  if (
    dueAt !== undefined &&
    dueAt > now.getTime() &&
    dueAt <= now.getTime() + DUE_WINDOW_MILLISECONDS &&
    PERSONALIZED_TASK_REMINDER_STATUSES.includes(
      assignment.status as PersonalizedTaskReminderStatus,
    )
  ) {
    reminders.push({
      teacherId: assignment.teacherId,
      typeCode: 'PERSONALIZED_TASK_DUE_24H',
      title: notificationTitle(title, assignment.taskCode, DUE_TITLE_SUFFIX),
      body: `This improvement task is due ${formatDueAt(assignment.dueAt!, assignment.timezoneUsed)}. Open it when you’re ready to continue or get started.`,
      actionType: 'TASK_DETAIL',
      actionTarget,
      expiresAt: assignment.dueAt,
      dedupeKey: `personalized-task-due-24h:${assignment.assignmentId}`,
      assignmentId: assignment.assignmentId,
      reminderType: 'DUE_24H',
    });
  }

  return reminders;
}
