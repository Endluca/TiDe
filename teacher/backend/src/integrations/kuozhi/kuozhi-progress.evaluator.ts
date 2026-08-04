import type { KuozhiCourseTask } from './kuozhi-course.config';
import type {
  KuozhiCourseDetail,
  KuozhiCourseDetailTask,
} from './kuozhi-detail.client';
import {
  kuozhiPassScoreKey,
  type KuozhiProgressCore,
  type KuozhiProgressTask,
  type KuozhiResolvedMapping,
} from './kuozhi.models';

function finiteNumber(value: unknown): number | null {
  if (typeof value !== 'number' && typeof value !== 'string') return null;
  if (typeof value === 'string' && value.trim() === '') return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function normalizedTasks(
  value: KuozhiCourseDetail['task_list'],
): Map<string, KuozhiCourseDetailTask> {
  if (!value) return new Map();
  const entries = Array.isArray(value)
    ? value.map((task) => [String(task.id ?? ''), task] as const)
    : Object.entries(value);
  return new Map(entries.filter(([id]) => /^\d+$/u.test(id)));
}

function passScore(
  task: Extract<KuozhiCourseTask, { type: 'TESTPAPER' }>,
  publishedPassScores: ReadonlyMap<string, number>,
): number | null {
  if (task.passScore.kind === 'FIXED') return task.passScore.percent;
  return (
    publishedPassScores.get(
      kuozhiPassScoreKey(
        task.passScore.bankKey,
        task.passScore.questionSetVersion,
      ),
    ) ?? null
  );
}

function evaluateTask(
  configured: KuozhiCourseTask,
  source: KuozhiCourseDetailTask | undefined,
  publishedPassScores: ReadonlyMap<string, number>,
): KuozhiProgressTask {
  const title = configured.title ?? `Task ${configured.courseTaskId}`;
  if (!source) {
    return {
      courseTaskId: configured.courseTaskId,
      title,
      type: configured.type,
      required: configured.required,
      sourceStatus: 'MISSING',
      percent: null,
      score: null,
      normalizedScorePercent: null,
      passScorePercent: null,
      testTimes: null,
      completed: false,
    };
  }

  const percent = finiteNumber(source.percent);
  if (configured.type === 'VIDEO') {
    const valid = percent !== null && percent >= 0 && percent <= 100;
    return {
      courseTaskId: configured.courseTaskId,
      title: source.title ?? title,
      type: configured.type,
      required: configured.required,
      sourceStatus: valid ? 'AVAILABLE' : 'INVALID',
      percent: valid ? percent : null,
      score: null,
      normalizedScorePercent: null,
      passScorePercent: null,
      testTimes: null,
      completed: valid && percent >= configured.completionPercent,
    };
  }

  const score = finiteNumber(source.score);
  const threshold = passScore(configured, publishedPassScores);
  const normalizedScorePercent =
    score === null
      ? null
      : configured.scoreMode === 'PERCENT'
        ? score
        : (score / configured.fullScore!) * 100;
  const valid =
    percent !== null &&
    percent >= 0 &&
    percent <= 100 &&
    score !== null &&
    normalizedScorePercent !== null &&
    normalizedScorePercent >= 0 &&
    threshold !== null &&
    threshold >= 0 &&
    threshold <= 100;

  return {
    courseTaskId: configured.courseTaskId,
    title: source.title ?? title,
    type: configured.type,
    required: configured.required,
    sourceStatus: valid ? 'AVAILABLE' : 'INVALID',
    percent:
      percent !== null && percent >= 0 && percent <= 100 ? percent : null,
    score,
    normalizedScorePercent: valid ? normalizedScorePercent : null,
    passScorePercent: threshold,
    testTimes: finiteNumber(source.test_times),
    completed: valid && normalizedScorePercent >= threshold,
  };
}

export function evaluateKuozhiProgress(
  resolved: KuozhiResolvedMapping,
  details: readonly KuozhiCourseDetail[],
  publishedPassScores: ReadonlyMap<string, number>,
  refreshedAt: string,
): KuozhiProgressCore {
  const courses = resolved.mapping.courses.map((configured, index) => {
    const detail = details[index];
    const sourceCourseId = String(detail?.id ?? '');
    const sourceAvailable = sourceCourseId === configured.courseId;
    const sourceTasks = normalizedTasks(
      sourceAvailable ? detail.task_list : null,
    );
    const tasks = configured.tasks.map((task) =>
      evaluateTask(
        task,
        sourceTasks.get(task.courseTaskId),
        publishedPassScores,
      ),
    );
    const requiredTasks = tasks.filter((task) => task.required);
    const coursePercent = sourceAvailable ? finiteNumber(detail.percent) : null;
    return {
      courseId: configured.courseId,
      title:
        detail?.title ?? configured.title ?? `Course ${configured.courseId}`,
      sourceAvailable,
      percent:
        coursePercent !== null && coursePercent >= 0 && coursePercent <= 100
          ? coursePercent
          : null,
      completed:
        sourceAvailable &&
        requiredTasks.length > 0 &&
        requiredTasks.every((task) => task.completed),
      tasks,
    };
  });

  const allTasks = courses.flatMap((course) => course.tasks);
  const requiredTasks = allTasks.filter((task) => task.required);
  const hasSource = courses.some((course) => course.sourceAvailable);
  const missing = requiredTasks.some((task) => task.sourceStatus === 'MISSING');
  const invalid = requiredTasks.some((task) => task.sourceStatus === 'INVALID');
  const requirementsMet =
    requiredTasks.length > 0 && requiredTasks.every((task) => task.completed);
  const syncStatus = !hasSource
    ? 'NO_DATA'
    : missing || invalid
      ? 'PARTIAL'
      : 'AVAILABLE';
  const completionEnabled = resolved.mapping.completionEnabled;
  const completed = completionEnabled && requirementsMet;
  const reasonCode = !completionEnabled
    ? 'COMPLETION_DISABLED'
    : !hasSource
      ? 'NO_DATA'
      : missing
        ? 'REQUIRED_TASK_MISSING'
        : invalid
          ? 'SOURCE_VALUE_INVALID'
          : requirementsMet
            ? 'COMPLETED'
            : 'REQUIREMENTS_INCOMPLETE';

  return {
    provider: 'KUOZHI',
    dataMode: resolved.dataMode,
    integrationStatus: resolved.mapping.integrationStatus,
    mappingVersion: resolved.mappingVersion,
    syncStatus,
    refreshedAt,
    courses,
    completion: { enabled: completionEnabled, completed, reasonCode },
  };
}
