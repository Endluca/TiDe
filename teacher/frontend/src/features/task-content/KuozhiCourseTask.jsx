import { useCallback, useEffect, useState } from 'react';
import {
  ArrowClockwise,
  CheckCircle,
  Clock,
  SpinnerGap,
  WarningCircle,
} from '@phosphor-icons/react';
import {
  getKuozhiLaunch,
  getKuozhiProgress,
  refreshKuozhiProgress,
} from '../../api/kuozhi-api';
import { localizeApiError } from '../../api-error-copy';
import { useI18n } from '../../i18n';
import './kuozhi-course-task.css';

function taskStatusCopy(task, c) {
  if (task.sourceStatus === 'MISSING') return c('Waiting for data', '等待同步');
  if (task.sourceStatus === 'INVALID') return c('Data unavailable', '数据异常');
  if (task.type === 'VIDEO') {
    return task.completed
      ? c('Watched', '已完播')
      : c(`${task.percent ?? 0}% watched`, `已观看 ${task.percent ?? 0}%`);
  }
  if (task.completed) {
    return c('Passed', '已通过');
  }
  if (task.normalizedScorePercent !== null) {
    return c(
      `${task.normalizedScorePercent}% · needs ${task.passScorePercent}%`,
      `${task.normalizedScorePercent}% · 通过线 ${task.passScorePercent}%`,
    );
  }
  return c('Not completed', '尚未完成');
}

function progressSummary(progress, c) {
  if (!progress || progress.syncStatus === 'NOT_SYNCED') {
    return c('Progress has not been synced yet.', '尚未同步学习进度。');
  }
  if (progress.syncStatus === 'NO_DATA') {
    return c(
      'No course data is available yet. Try again later.',
      '暂未同步到课程数据，请稍后重试。',
    );
  }
  if (progress.syncStatus === 'PARTIAL') {
    return c(
      'Some required course data is still missing.',
      '部分必修课程数据仍未同步完整。',
    );
  }
  if (progress.completion.completed) {
    return c('All required learning is complete.', '全部必修学习内容已完成。');
  }
  if (!progress.completion.enabled) {
    return c(
      'Progress is visible; automatic task completion is not enabled yet.',
      '学习进度可查看，当前任务暂未开放自动完成。',
    );
  }
  return c(
    'Complete all required videos and assessments, then refresh.',
    '请完成全部必修视频和考试后刷新进度。',
  );
}

function initialCourseId(courses, progress) {
  if (!courses.length) return null;
  const progressByCourse = new Map(
    (progress?.courses ?? []).map((course) => [course.courseId, course]),
  );
  return (
    courses.find((course) => !progressByCourse.get(course.courseId)?.completed)
    ?? courses[0]
  ).courseId;
}

function courseStatusCopy(course, progress, c) {
  const courseProgress = progress?.courses?.find(
    (item) => item.courseId === course.courseId,
  );
  if (!courseProgress || !courseProgress.sourceAvailable) {
    return c('Waiting to sync', '等待同步');
  }
  if (courseProgress.completed) return c('Completed', '已完成');
  if (courseProgress.percent !== null) return `${courseProgress.percent}%`;
  return c('In progress', '进行中');
}

export default function KuozhiCourseTask({ task }) {
  const { language } = useI18n();
  const [launch, setLaunch] = useState(null);
  const [progress, setProgress] = useState(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [launchError, setLaunchError] = useState('');
  const [progressError, setProgressError] = useState('');
  const [activeCourseId, setActiveCourseId] = useState(null);
  const [visitedCourseIds, setVisitedCourseIds] = useState(() => new Set());
  const c = useCallback((en, zh) => (language === 'zh' ? zh : en), [language]);

  const refreshProgress = useCallback(async (signal, currentProgress = progress) => {
    setRefreshing(true);
    setProgressError('');
    try {
      const latestContext = await task.execution?.refresh?.().catch(() => null);
      const stateVersion =
        latestContext?.stateVersion
        ?? currentProgress?.assignment?.stateVersion
        ?? task.stateVersion;
      const response = await refreshKuozhiProgress(
        task.backendId,
        stateVersion,
        signal,
      );
      setProgress(response);
      if (response.assignment?.stateUpdated) {
        await task.execution?.refresh?.().catch(() => undefined);
      }
      return response;
    } catch (caught) {
      if (caught?.name === 'AbortError') return null;
      setProgressError(
        localizeApiError(
          caught,
          language,
          c('Progress could not be synced. Please try again.', '学习进度同步失败，请重试。'),
        ),
      );
      return null;
    } finally {
      setRefreshing(false);
    }
  }, [c, language, progress, task]);

  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    setLoading(true);
    setLaunchError('');
    setProgressError('');
    setActiveCourseId(null);
    setVisitedCourseIds(new Set());

    const load = async () => {
      try {
        const launchResponse = await getKuozhiLaunch(
          task.backendId,
          controller.signal,
        );
        await task.execution?.start?.();
        const latestProgress = await getKuozhiProgress(
          task.backendId,
          controller.signal,
        );
        if (!active) return;
        setLaunch(launchResponse);
        setProgress(latestProgress);
        const firstCourseId = initialCourseId(
          launchResponse.courses ?? [],
          latestProgress,
        );
        setActiveCourseId(firstCourseId);
        setVisitedCourseIds(new Set(firstCourseId ? [firstCourseId] : []));
        await refreshProgress(controller.signal, latestProgress);
      } catch (caught) {
        if (!active || caught?.name === 'AbortError') return;
        setLaunchError(
          localizeApiError(
            caught,
            language,
            c('The course could not be opened. Please try again.', '课程暂时无法打开，请重试。'),
          ),
        );
      } finally {
        if (active) setLoading(false);
      }
    };

    void load();
    return () => {
      active = false;
      controller.abort();
    };
    // Initial loading belongs to the task instance. Manual refresh is separate.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [task.backendId]);

  const courses = launch?.courses ?? [];
  const hasMultipleCourses = courses.length > 1;
  const selectedCourseId = courses.some(
    (course) => course.courseId === activeCourseId,
  )
    ? activeCourseId
    : courses[0]?.courseId ?? null;

  const selectCourse = (courseId) => {
    setActiveCourseId(courseId);
    setVisitedCourseIds((current) => {
      if (current.has(courseId)) return current;
      const next = new Set(current);
      next.add(courseId);
      return next;
    });
  };

  return (
    <div className="kuozhi-course-flow">
      {launchError && (
        <div className="kuozhi-course-error" role="alert">
          <WarningCircle size={26} weight="fill" />
          <span>{launchError}</span>
        </div>
      )}

      {hasMultipleCourses && (
        <div
          className="kuozhi-course-tabs"
          role="tablist"
          aria-label={c('Training courses', '培训课程')}
        >
          {courses.map((course, index) => {
            const isActive = course.courseId === selectedCourseId;
            const title = course.title || c(`Course ${index + 1}`, `课程 ${index + 1}`);
            const completed = progress?.courses?.find(
              (item) => item.courseId === course.courseId,
            )?.completed;
            return (
              <button
                type="button"
                role="tab"
                aria-selected={isActive}
                aria-controls={`kuozhi-course-panel-${course.courseId}`}
                id={`kuozhi-course-tab-${course.courseId}`}
                className={isActive ? 'is-active' : ''}
                key={course.courseId}
                onClick={() => selectCourse(course.courseId)}
              >
                {completed
                  ? <CheckCircle size={19} weight="fill" />
                  : <Clock size={19} weight="fill" />}
                <span>
                  <strong>{title}</strong>
                  <small>{courseStatusCopy(course, progress, c)}</small>
                </span>
              </button>
            );
          })}
        </div>
      )}

      <div className="kuozhi-course-grid">
        {courses.map((course, index) => {
          const courseTitle = course.title
            || c(`Course ${index + 1}`, `课程 ${index + 1}`);
          const isActive = course.courseId === selectedCourseId;
          const shouldMount = isActive || visitedCourseIds.has(course.courseId);
          if (!shouldMount) return null;
          return (
            <section
              className="kuozhi-course-entry"
              id={`kuozhi-course-panel-${course.courseId}`}
              role={hasMultipleCourses ? 'tabpanel' : undefined}
              aria-labelledby={hasMultipleCourses
                ? `kuozhi-course-tab-${course.courseId}`
                : undefined}
              hidden={!isActive}
              key={course.courseId}
            >
              <div className="kuozhi-embed-shell kuozhi-embed-shell--hide-navigation">
                <iframe
                  src={course.launchUrl}
                  title={c(`${courseTitle} embedded course`, `${courseTitle} 内嵌课程`)}
                  allow="fullscreen"
                  allowFullScreen
                  referrerPolicy="strict-origin-when-cross-origin"
                />
              </div>
            </section>
          );
        })}
        {loading && (
          <div className="kuozhi-loading-row">
            <SpinnerGap size={26} weight="bold" />
            {c('Loading courses…', '正在加载课程…')}
          </div>
        )}
      </div>

      <section className="kuozhi-progress-panel">
        <div className="kuozhi-progress-heading">
          <div>
            <strong>{c('Learning progress', '学习进度')}</strong>
            <p>{progressSummary(progress, c)}</p>
          </div>
          <button
            type="button"
            onClick={() => void refreshProgress()}
            disabled={refreshing || !progress}
          >
            {refreshing
              ? <SpinnerGap size={18} className="kuozhi-spin" />
              : <ArrowClockwise size={18} />}
            {refreshing ? c('Syncing…', '同步中…') : c('Refresh progress', '刷新学习进度')}
          </button>
        </div>

        {progressError && (
          <div className="kuozhi-progress-error" role="alert">{progressError}</div>
        )}

        <div className="kuozhi-progress-courses">
          {(progress?.courses ?? []).map((course) => (
            <div className="kuozhi-progress-course" key={course.courseId}>
              <div className="kuozhi-progress-course-title">
                <strong>{course.title}</strong>
                <span>{course.percent === null ? '—' : `${course.percent}%`}</span>
              </div>
              <div className="kuozhi-progress-tasks">
                {course.tasks.map((courseTask) => (
                  <div className="kuozhi-progress-task" key={courseTask.courseTaskId}>
                    {courseTask.completed
                      ? <CheckCircle size={21} weight="fill" />
                      : <Clock size={21} weight="fill" />}
                    <div>
                      <strong>{courseTask.title}</strong>
                      <small>{taskStatusCopy(courseTask, c)}</small>
                    </div>
                    <span>{courseTask.type === 'VIDEO' ? c('Video', '视频') : c('Assessment', '考试')}</span>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>

        {progress?.refreshedAt && (
          <small className="kuozhi-refreshed-at">
            {c('Last synced', '最近同步')}：{new Date(progress.refreshedAt).toLocaleString()}
          </small>
        )}
      </section>
    </div>
  );
}
