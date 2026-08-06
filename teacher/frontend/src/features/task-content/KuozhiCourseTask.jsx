import { useCallback, useEffect, useState } from 'react';
import {
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
import KuozhiProgressCard from './KuozhiProgressCard';
import './kuozhi-course-task.css';

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

export default function KuozhiCourseTask({ task, onProgressStateChange }) {
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

  useEffect(() => {
    onProgressStateChange?.({
      loading,
      onRefresh: refreshProgress,
      progress,
      progressError,
      refreshing,
    });
  }, [loading, onProgressStateChange, progress, progressError, refreshProgress, refreshing]);

  useEffect(() => () => {
    onProgressStateChange?.(null);
  }, [onProgressStateChange]);

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

      <KuozhiProgressCard
        className="kuozhi-progress-card--mobile"
        loading={loading}
        onRefresh={refreshProgress}
        progress={progress}
        progressError={progressError}
        refreshing={refreshing}
      />
    </div>
  );
}
