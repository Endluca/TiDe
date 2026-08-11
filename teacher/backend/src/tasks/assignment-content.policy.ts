export const PERSONALIZED_ENVIRONMENT_PHOTO_TASK_CODE = 'P-FB-NEGATIVE';
export const PERSONALIZED_ENVIRONMENT_PHOTO_VARIANT =
  'TEACHING_ENVIRONMENT_PHOTO';
export const PERSONALIZED_ENVIRONMENT_PHOTO_STEP_KEY =
  'p-fb-negative-environment-photo';

const personalizedEnvironmentPhotoLabels = new Set([
  '灯光过暗/亮',
  '环境乱/灯光差',
]);

export interface AssignmentContentCandidate {
  taskCode: string;
  contentConfig: Record<string, unknown>;
  evidenceSnapshot: unknown;
}

export function resolveAssignmentContent<T extends AssignmentContentCandidate>(
  task: T,
): T {
  if (
    task.taskCode !== PERSONALIZED_ENVIRONMENT_PHOTO_TASK_CODE ||
    task.contentConfig.contentStatus !== 'PENDING' ||
    !matchesPersonalizedEnvironmentPhotoVariant(task.evidenceSnapshot)
  ) {
    return task;
  }
  return {
    ...task,
    contentConfig: {
      ...task.contentConfig,
      contentStatus: 'READY',
      pendingReason: null,
      contentVariant: PERSONALIZED_ENVIRONMENT_PHOTO_VARIANT,
    },
  };
}

export function isTaskUploadStepAuthorized(
  task: AssignmentContentCandidate,
  stepKey: string,
): boolean {
  const resolved = resolveAssignmentContent(task);
  if (resolved.contentConfig.contentStatus !== 'READY') {
    return false;
  }

  const isPersonalizedEnvironmentTask =
    resolved.taskCode === PERSONALIZED_ENVIRONMENT_PHOTO_TASK_CODE;
  const isPersonalizedEnvironmentStep =
    stepKey === PERSONALIZED_ENVIRONMENT_PHOTO_STEP_KEY;
  if (!isPersonalizedEnvironmentTask && !isPersonalizedEnvironmentStep) {
    return true;
  }

  return (
    isPersonalizedEnvironmentTask &&
    isPersonalizedEnvironmentStep &&
    resolved.contentConfig.contentVariant ===
      PERSONALIZED_ENVIRONMENT_PHOTO_VARIANT
  );
}

function matchesPersonalizedEnvironmentPhotoVariant(value: unknown): boolean {
  if (!isRecord(value)) {
    return false;
  }
  if (
    Object.prototype.hasOwnProperty.call(value, 'teacher_execution_variant')
  ) {
    return (
      value.teacher_execution_variant === PERSONALIZED_ENVIRONMENT_PHOTO_VARIANT
    );
  }
  if (!Array.isArray(value.signal_samples)) {
    return false;
  }
  return value.signal_samples.some((sample) => {
    if (!isRecord(sample) || !isRecord(sample.evidence)) {
      return false;
    }
    const label = sample.evidence.negative_feedback_label;
    return (
      typeof label === 'string' && personalizedEnvironmentPhotoLabels.has(label)
    );
  });
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}
