import {
  ArrowClockwise,
  CaretDown,
  CheckCircle,
  Clock,
  SpinnerGap,
  WarningCircle,
} from '@phosphor-icons/react';
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
  if (task.completed) return c('Passed', '已通过');
  if (typeof task.normalizedScorePercent === 'number') {
    return c(
      `${task.normalizedScorePercent}% · needs ${task.passScorePercent}%`,
      `${task.normalizedScorePercent}% · 通过线 ${task.passScorePercent}%`,
    );
  }
  return c('Not completed', '尚未完成');
}

function progressSummary(progress, c) {
  if (!progress || progress.syncStatus === 'NOT_SYNCED') {
    return c('Not synced yet', '尚未同步');
  }
  if (progress.syncStatus === 'NO_DATA') {
    return c('No course data yet', '暂未同步到课程数据');
  }
  if (progress.syncStatus === 'PARTIAL') {
    return c('Some data is still syncing', '部分数据仍在同步');
  }
  if (progress.completion?.completed) {
    return c('All required learning is complete', '全部必修学习已完成');
  }
  if (progress.completion?.enabled === false) {
    return c('Progress is available', '可查看学习进度');
  }
  return c('Learning in progress', '学习进行中');
}

function safePercent(value) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? Math.max(0, Math.min(100, numeric)) : null;
}

export default function KuozhiProgressCard({
  className = '',
  loading = false,
  onRefresh,
  progress,
  progressError = '',
  refreshing = false,
}) {
  const { language } = useI18n();
  const c = (en, zh) => (language === 'zh' ? zh : en);
  const courses = progress?.courses ?? [];
  const courseTasks = courses.flatMap((course) => course.tasks ?? []);
  const completedTasks = courseTasks.filter((task) => task.completed).length;
  const completionPercent = courseTasks.length
    ? Math.round((completedTasks / courseTasks.length) * 100)
    : 0;

  return (
    <section className={`kuozhi-progress-card ${className}`.trim()} aria-live="polite">
      <header className="kuozhi-progress-card-header">
        <div>
          <span>{c('Learning progress', '学习进度')}</span>
          <strong>{progressSummary(progress, c)}</strong>
        </div>
        <button
          type="button"
          onClick={() => void onRefresh?.()}
          disabled={refreshing || !progress}
          aria-label={refreshing ? c('Syncing progress', '正在同步学习进度') : c('Refresh progress', '刷新学习进度')}
          title={refreshing ? c('Syncing…', '同步中…') : c('Refresh progress', '刷新学习进度')}
        >
          {refreshing
            ? <SpinnerGap size={18} className="kuozhi-spin" />
            : <ArrowClockwise size={18} />}
        </button>
      </header>

      <div className="kuozhi-progress-overview">
        <div>
          <strong>{courseTasks.length ? `${completedTasks}/${courseTasks.length}` : '—'}</strong>
          <small>{c('required items complete', '项必修内容已完成')}</small>
        </div>
        <div className="kuozhi-progress-track" aria-hidden="true">
          <i style={{ width: `${completionPercent}%` }} />
        </div>
      </div>

      {progressError && (
        <div className="kuozhi-progress-error" role="alert">
          <WarningCircle size={18} weight="fill" />
          <span>{progressError}</span>
        </div>
      )}

      {loading && !progress && (
        <div className="kuozhi-progress-empty">
          <SpinnerGap size={18} className="kuozhi-spin" />
          {c('Loading progress…', '正在加载进度…')}
        </div>
      )}

      {!loading && courses.length === 0 && (
        <div className="kuozhi-progress-empty">
          <Clock size={18} weight="fill" />
          {c('Waiting for the first sync', '等待首次同步')}
        </div>
      )}

      <div className="kuozhi-progress-course-list">
        {courses.map((course, index) => {
          const percent = safePercent(course.percent);
          const tasks = course.tasks ?? [];
          const completed = tasks.filter((task) => task.completed).length;
          return (
            <details
              className="kuozhi-progress-course-item"
              defaultOpen={courses.length === 1 && index === 0}
              key={course.courseId}
            >
              <summary>
                <div>
                  <strong>{course.title}</strong>
                  <small>{c(`${completed}/${tasks.length} complete`, `已完成 ${completed}/${tasks.length}`)}</small>
                  <span className="kuozhi-course-progress-track" aria-hidden="true">
                    <i style={{ width: `${percent ?? 0}%` }} />
                  </span>
                </div>
                <b>{percent === null ? '—' : `${percent}%`}</b>
                <CaretDown size={16} weight="bold" />
              </summary>
              <div className="kuozhi-progress-task-list">
                {tasks.map((courseTask) => (
                  <div className="kuozhi-progress-task-row" key={courseTask.courseTaskId}>
                    {courseTask.completed
                      ? <CheckCircle size={18} weight="fill" />
                      : <Clock size={18} weight="fill" />}
                    <div>
                      <strong>{courseTask.title}</strong>
                      <small>{taskStatusCopy(courseTask, c)}</small>
                    </div>
                  </div>
                ))}
              </div>
            </details>
          );
        })}
      </div>

      {progress?.refreshedAt && (
        <small className="kuozhi-refreshed-at">
          {c('Last synced', '最近同步')}：{new Date(progress.refreshedAt).toLocaleString()}
        </small>
      )}
    </section>
  );
}
