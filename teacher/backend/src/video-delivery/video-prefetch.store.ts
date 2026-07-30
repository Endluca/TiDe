import { createHash, randomUUID } from 'node:crypto';
import {
  appendFile,
  mkdir,
  open,
  readFile,
  readdir,
  rename,
  stat,
  unlink,
  writeFile,
} from 'node:fs/promises';
import { resolve } from 'node:path';
import {
  VideoPrefetchLogEntry,
  VideoPrefetchRun,
  VideoPrefetchTrigger,
} from './video-prefetch.types';

const staleLockMilliseconds = 2 * 60 * 60 * 1000;

function isMissing(error: unknown): boolean {
  return (error as NodeJS.ErrnoException)?.code === 'ENOENT';
}

export function videoPrefetchIdempotencyKey(input: {
  trigger: VideoPrefetchTrigger;
  eventId: string;
  manifestVersion: string;
  urls: string[];
}): string {
  return createHash('sha256')
    .update(
      JSON.stringify({
        trigger: input.trigger,
        eventId: input.eventId,
        manifestVersion: input.manifestVersion,
        urls: [...input.urls].sort(),
      }),
    )
    .digest('hex');
}

export class VideoPrefetchStore {
  constructor(private readonly rootDirectory: string) {}

  private runPath(idempotencyKey: string): string {
    return resolve(this.rootDirectory, 'runs', `${idempotencyKey}.json`);
  }

  private lockPath(idempotencyKey: string): string {
    return resolve(this.rootDirectory, 'locks', `${idempotencyKey}.lock`);
  }

  async read(idempotencyKey: string): Promise<VideoPrefetchRun | undefined> {
    try {
      return JSON.parse(
        await readFile(this.runPath(idempotencyKey), 'utf8'),
      ) as VideoPrefetchRun;
    } catch (error) {
      if (isMissing(error)) return undefined;
      throw error;
    }
  }

  async save(run: VideoPrefetchRun): Promise<void> {
    const path = this.runPath(run.idempotencyKey);
    const temporaryPath = `${path}.${process.pid}.tmp`;
    await mkdir(resolve(this.rootDirectory, 'runs'), { recursive: true });
    await writeFile(temporaryPath, `${JSON.stringify(run, null, 2)}\n`, {
      encoding: 'utf8',
      mode: 0o600,
    });
    await rename(temporaryPath, path);
  }

  async append(entry: VideoPrefetchLogEntry): Promise<void> {
    await mkdir(this.rootDirectory, { recursive: true });
    await appendFile(
      resolve(this.rootDirectory, 'events.jsonl'),
      `${JSON.stringify(entry)}\n`,
      { encoding: 'utf8', mode: 0o600 },
    );
  }

  async createRun(input: {
    idempotencyKey: string;
    trigger: VideoPrefetchTrigger;
    eventId: string;
    manifestVersion: string;
    urls: string[];
    now: string;
  }): Promise<VideoPrefetchRun> {
    const run: VideoPrefetchRun = {
      runId: randomUUID(),
      idempotencyKey: input.idempotencyKey,
      trigger: input.trigger,
      eventId: input.eventId,
      manifestVersion: input.manifestVersion,
      urls: input.urls,
      taskIds: [],
      attempts: 0,
      status: 'PENDING',
      createdAt: input.now,
      updatedAt: input.now,
    };
    await this.save(run);
    return run;
  }

  async acquire(idempotencyKey: string): Promise<() => Promise<void>> {
    const locksDirectory = resolve(this.rootDirectory, 'locks');
    const path = this.lockPath(idempotencyKey);
    await mkdir(locksDirectory, { recursive: true });
    try {
      const handle = await open(path, 'wx', 0o600);
      await handle.writeFile(
        JSON.stringify({
          pid: process.pid,
          createdAt: new Date().toISOString(),
        }),
      );
      await handle.close();
    } catch (error) {
      if ((error as NodeJS.ErrnoException)?.code !== 'EEXIST') throw error;
      const lockStat = await stat(path);
      if (Date.now() - lockStat.mtimeMs <= staleLockMilliseconds) {
        throw new Error(`相同预热事件正在执行：${idempotencyKey}`, {
          cause: error,
        });
      }
      await unlink(path);
      return this.acquire(idempotencyKey);
    }
    return async () => {
      try {
        await unlink(path);
      } catch (error) {
        if (!isMissing(error)) throw error;
      }
    };
  }

  async hasSuccessfulNewRelease(manifestVersion: string): Promise<boolean> {
    const keyPrefix = resolve(this.rootDirectory, 'runs');
    let names: string[];
    try {
      names = await readdir(keyPrefix);
    } catch (error) {
      if (isMissing(error)) return false;
      throw error;
    }
    for (const name of names.filter((value) => value.endsWith('.json'))) {
      const run = JSON.parse(
        await readFile(resolve(keyPrefix, name), 'utf8'),
      ) as VideoPrefetchRun;
      if (
        run.trigger === 'NEW_RELEASE' &&
        run.manifestVersion === manifestVersion &&
        run.status === 'COMPLETE'
      ) {
        return true;
      }
    }
    return false;
  }
}
