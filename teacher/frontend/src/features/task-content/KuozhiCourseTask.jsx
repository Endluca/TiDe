import { useCallback, useEffect, useState } from 'react';
import {
  ArrowClockwise,
  CheckCircle,
  Clock,
  GraduationCap,
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

export default function KuozhiCourseTask({ task }) {
  const { language } = useI18n();
  const [launch, setLaunch] = useState(null);
  const [progress, setProgress] = useState(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [launchError, setLaunchError] = useState('');
  const [progressError, setProgressError] = useState('');
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

    const load = async () => {
      try {
        const launchResponse = await getKuozhiLaunch(
          task.backendId,
          controller.signal,
        );
        if (launchResponse.dataMode === 'REAL') {
          await task.execution?.start?.();
        }
        const latestProgress = await getKuozhiProgress(
          task.backendId,
          controller.signal,
        );
        if (!active) return;
        setLaunch(launchResponse);
        setProgress(latestProgress);
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

  return (
    <div className="kuozhi-course-flow">
      <div className="kuozhi-course-toolbar">
        <div>
          <span className="kuozhi-course-icon">
            <GraduationCap size={22} weight="fill" />
          </span>
          <div>
            <strong>{c('Training courses', '培训课程')}</strong>
            <small>
              {loading
                ? c('Preparing secure access…', '正在准备安全访问…')
                : c(`${launch?.courses?.length ?? 0} course(s)`, `${launch?.courses?.length ?? 0} 门课程`)}
            </small>
          </div>
        </div>
        {launch?.dataMode === 'SAMPLE_DRY_RUN' && (
          <span className="kuozhi-sample-badge">{c('Sample dry run', '示例联调')}</span>
        )}
      </div>

      {launch?.dataMode === 'SAMPLE_DRY_RUN' && (
        <div className="kuozhi-sample-note" role="note">
          <WarningCircle size={20} weight="fill" />
          <span>
            {c(
              'This is sample course data for integration testing. It will not change the current teacher task status.',
              '当前展示的是示例课程数据，只用于联调，不会修改当前老师的任务状态。',
            )}
          </span>
        </div>
      )}

      {launchError && (
        <div className="kuozhi-course-error" role="alert">
          <WarningCircle size={26} weight="fill" />
          <span>{launchError}</span>
        </div>
      )}

      <div className="kuozhi-course-grid">
        {(launch?.courses ?? []).map((course) => {
          const courseTitle = course.title
            || c(`Course ${course.courseId}`, `课程 ${course.courseId}`);
          return (
            <section className="kuozhi-course-entry" key={course.courseId}>
              <article className="kuozhi-launch-card">
                <div>
                  <strong>{courseTitle}</strong>
                  <small>{c(`Course ID ${course.courseId}`, `课程 ID ${course.courseId}`)}</small>
                </div>
              </article>
              <div className="kuozhi-embed-shell">
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
