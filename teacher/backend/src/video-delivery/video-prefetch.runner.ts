import {
  VideoPrefetchLogEntry,
  VideoPrefetchProvider,
  VideoPrefetchRun,
  VideoPrefetchTaskGroup,
  VideoPrefetchTrigger,
} from './video-prefetch.types';
import {
  VideoPrefetchStore,
  videoPrefetchIdempotencyKey,
} from './video-prefetch.store';

export interface RunVideoPrefetchInput {
  trigger: VideoPrefetchTrigger;
  eventId: string;
  manifestVersion: string;
  urls: string[];
  domainName: string;
  l2Preload?: boolean;
  maxAttempts?: number;
  retryFailed?: boolean;
  pollIntervalMilliseconds?: number;
  timeoutMilliseconds?: number;
}

export interface VideoPrefetchRunnerDependencies {
  provider: VideoPrefetchProvider;
  store: VideoPrefetchStore;
  now?: () => Date;
  sleep?: (milliseconds: number) => Promise<void>;
}

function errorMessage(error: unknown): string {
  const candidate =
    error && typeof error === 'object'
      ? (error as { message?: string; code?: string; requestId?: string })
      : {};
  return [
    candidate.message || String(error),
    candidate.code,
    candidate.requestId,
  ]
    .filter(Boolean)
    .join(' | ');
}

function isPermanentPermissionError(error: unknown): boolean {
  const candidate =
    error && typeof error === 'object'
      ? (error as { code?: string; message?: string; statusCode?: number })
      : {};
  return (
    candidate.statusCode === 403 ||
    candidate.code?.includes('Forbidden') === true ||
    candidate.message?.includes('not authorized') === true
  );
}

export class VideoPrefetchRunner {
  private readonly now: () => Date;
  private readonly sleep: (milliseconds: number) => Promise<void>;

  constructor(private readonly dependencies: VideoPrefetchRunnerDependencies) {
    this.now = dependencies.now || (() => new Date());
    this.sleep =
      dependencies.sleep ||
      ((milliseconds) =>
        new Promise((resolveDelay) => setTimeout(resolveDelay, milliseconds)));
  }

  private async log(
    run: VideoPrefetchRun,
    entry: Omit<
      VideoPrefetchLogEntry,
      | 'time'
      | 'runId'
      | 'idempotencyKey'
      | 'trigger'
      | 'eventId'
      | 'manifestVersion'
    >,
  ): Promise<void> {
    await this.dependencies.store.append({
      time: this.now().toISOString(),
      runId: run.runId,
      idempotencyKey: run.idempotencyKey,
      trigger: run.trigger,
      eventId: run.eventId,
      manifestVersion: run.manifestVersion,
      ...entry,
    });
  }

  private async update(
    run: VideoPrefetchRun,
    patch: Partial<VideoPrefetchRun>,
  ): Promise<void> {
    Object.assign(run, patch, { updatedAt: this.now().toISOString() });
    await this.dependencies.store.save(run);
  }

  private async logObservation(
    run: VideoPrefetchRun,
    groups: VideoPrefetchTaskGroup[],
  ): Promise<void> {
    for (const group of groups) {
      if (group.tasks.length === 0) {
        await this.log(run, {
          status: 'PENDING',
          taskId: group.taskId,
          attempt: run.attempts,
        });
      }
      for (const task of group.tasks) {
        await this.log(run, {
          status: task.status || 'UNKNOWN',
          taskId: task.taskId || group.taskId,
          url: task.url,
          process: task.process,
          attempt: run.attempts,
          message: task.description,
        });
      }
    }
  }

  private async waitForCompletion(
    run: VideoPrefetchRun,
    input: Required<
      Pick<
        RunVideoPrefetchInput,
        'domainName' | 'pollIntervalMilliseconds' | 'timeoutMilliseconds'
      >
    >,
  ): Promise<'COMPLETE' | 'FAILED'> {
    const deadline = this.now().getTime() + input.timeoutMilliseconds;
    await this.update(run, { status: 'POLLING' });
    while (this.now().getTime() < deadline) {
      let groups: VideoPrefetchTaskGroup[];
      try {
        groups = await this.dependencies.provider.describe(
          run.taskIds,
          input.domainName,
        );
      } catch (error) {
        await this.log(run, {
          status: 'QUERY_ERROR',
          attempt: run.attempts,
          message: errorMessage(error),
        });
        if (isPermanentPermissionError(error)) throw error;
        await this.sleep(input.pollIntervalMilliseconds);
        continue;
      }
      await this.logObservation(run, groups);
      const tasks = groups.flatMap((group) => group.tasks);
      if (tasks.some((task) => task.status === 'Failed')) return 'FAILED';
      if (
        groups.length === run.taskIds.length &&
        groups.every(
          (group) =>
            group.tasks.length > 0 &&
            group.tasks.every((task) => task.status === 'Complete'),
        )
      ) {
        for (const url of run.urls) {
          await this.log(run, {
            status: 'COMPLETE',
            url,
            taskId: run.taskIds.join(','),
            process: '100%',
            attempt: run.attempts,
          });
        }
        return 'COMPLETE';
      }
      await this.sleep(input.pollIntervalMilliseconds);
    }
    await this.log(run, {
      status: 'POLL_TIMEOUT',
      taskId: run.taskIds.join(','),
      attempt: run.attempts,
      message: '查询超时；下次执行会继续查询同一批任务，不会重复提交',
    });
    throw new Error('CDN 预热查询超时，任务仍保留为可续查状态');
  }

  async run(input: RunVideoPrefetchInput): Promise<VideoPrefetchRun> {
    const urls = [...new Set(input.urls)].sort();
    if (urls.length === 0) throw new Error('预热 URL 不能为空');
    if (urls.length > 100) {
      throw new Error(`单次预热最多 100 个 URL，当前 ${urls.length} 个`);
    }
    const idempotencyKey = videoPrefetchIdempotencyKey({
      trigger: input.trigger,
      eventId: input.eventId,
      manifestVersion: input.manifestVersion,
      urls,
    });
    const release = await this.dependencies.store.acquire(idempotencyKey);
    try {
      const now = this.now().toISOString();
      const existing = await this.dependencies.store.read(idempotencyKey);
      const run =
        existing ||
        (await this.dependencies.store.createRun({
          idempotencyKey,
          trigger: input.trigger,
          eventId: input.eventId,
          manifestVersion: input.manifestVersion,
          urls,
          now,
        }));
      if (run.status === 'COMPLETE') {
        await this.log(run, {
          status: 'IDEMPOTENT_HIT',
          taskId: run.taskIds.join(','),
          message: '相同事件已成功，跳过重复提交',
        });
        return run;
      }
      if (run.status === 'FAILED' && !input.retryFailed) {
        throw new Error('相同事件上次已失败；修复后添加 --retry-failed 重试');
      }

      const maxAttempts = input.maxAttempts || 3;
      const targetAttempts =
        run.status === 'FAILED' && input.retryFailed
          ? run.attempts + maxAttempts
          : Math.max(run.attempts, maxAttempts);
      const pollOptions = {
        domainName: input.domainName,
        pollIntervalMilliseconds: input.pollIntervalMilliseconds || 15_000,
        timeoutMilliseconds: input.timeoutMilliseconds || 45 * 60_000,
      };

      if (
        run.taskIds.length > 0 &&
        (run.status === 'SUBMITTED' ||
          run.status === 'POLLING' ||
          (run.status === 'FAILED' && input.retryFailed))
      ) {
        const result = await this.waitForCompletion(run, pollOptions);
        if (result === 'COMPLETE') {
          await this.update(run, {
            status: 'COMPLETE',
            completedAt: this.now().toISOString(),
            lastError: undefined,
          });
          return run;
        }
        await this.update(run, {
          status: 'PENDING',
          taskIds: [],
          lastError: '阿里云返回预热任务失败',
        });
      }

      while (run.attempts < targetAttempts) {
        await this.update(run, {
          status: 'PENDING',
          attempts: run.attempts + 1,
          startedAt: run.startedAt || this.now().toISOString(),
          lastError: undefined,
        });
        try {
          const submitted = await this.dependencies.provider.submit(urls, {
            area: 'overseas',
            l2Preload: input.l2Preload || false,
          });
          if (submitted.taskIds.length === 0) {
            throw new Error('阿里云未返回 CDN 预热任务 ID');
          }
          await this.update(run, {
            status: 'SUBMITTED',
            taskIds: submitted.taskIds,
            requestId: submitted.requestId,
          });
          for (const url of urls) {
            await this.log(run, {
              status: 'SUBMITTED',
              url,
              taskId: submitted.taskIds.join(','),
              attempt: run.attempts,
            });
          }
          const result = await this.waitForCompletion(run, pollOptions);
          if (result === 'COMPLETE') {
            await this.update(run, {
              status: 'COMPLETE',
              completedAt: this.now().toISOString(),
              lastError: undefined,
            });
            return run;
          }
          throw new Error('阿里云返回预热任务失败');
        } catch (error) {
          const message = errorMessage(error);
          await this.log(run, {
            status: 'ATTEMPT_FAILED',
            taskId: run.taskIds.join(','),
            attempt: run.attempts,
            message,
          });
          if (run.status === 'POLLING' && message.includes('查询超时')) {
            throw error;
          }
          if (run.status === 'POLLING' && isPermanentPermissionError(error)) {
            await this.update(run, {
              status: 'FAILED',
              lastError: message,
            });
            break;
          }
          await this.update(run, {
            status: 'PENDING',
            taskIds: [],
            lastError: message,
          });
          if (isPermanentPermissionError(error)) break;
          if (run.attempts < targetAttempts) {
            await this.sleep(Math.min(10_000, 1_000 * 2 ** run.attempts));
          }
        }
      }
      await this.update(run, { status: 'FAILED' });
      throw new Error(run.lastError || 'CDN 预热失败');
    } finally {
      await release();
    }
  }
}
