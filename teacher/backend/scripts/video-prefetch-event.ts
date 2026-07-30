import { spawn } from 'node:child_process';
import { appendFile, mkdir } from 'node:fs/promises';
import { resolve } from 'node:path';
import { config as loadEnvironment } from 'dotenv';
import { AliyunVideoPrefetchProvider } from '../src/video-delivery/aliyun-video-prefetch.provider';
import { VideoPrefetchRunner } from '../src/video-delivery/video-prefetch.runner';
import { VideoPrefetchStore } from '../src/video-delivery/video-prefetch.store';
import { VideoPrefetchTrigger } from '../src/video-delivery/video-prefetch.types';
import {
  assertReleaseFilesExist,
  publicVideoUrl,
  readVideoReleaseManifest,
  releaseVideoUrls,
  verifyVideoRangeUrls,
} from '../src/video-delivery/video-release-manifest';
import {
  currentReleaseVideoPaths,
  switchReleaseVideoBindings,
} from '../src/video-delivery/video-release.repository';

const backendRoot = resolve(__dirname, '..');
const defaultManifestPath = resolve(
  backendRoot,
  'config/public-videos-v2.json',
);
const defaultSourceRoot = resolve(backendRoot, '../.video-v2-media/output');
const defaultStateDirectory = resolve(
  backendRoot,
  'storage/video-prefetch-runs',
);

interface PipelineLog {
  time: string;
  trigger: VideoPrefetchTrigger;
  eventId: string;
  manifestVersion: string;
  status: string;
  message?: string;
}

function argument(argv: string[], name: string): string | undefined {
  const prefix = `${name}=`;
  return argv.find((value) => value.startsWith(prefix))?.slice(prefix.length);
}

function positiveInteger(value: string | undefined, fallback: number): number {
  const parsed = Number(value);
  return Number.isInteger(parsed) && parsed > 0 ? parsed : fallback;
}

function parseTrigger(value: string | undefined): VideoPrefetchTrigger {
  const normalized = value?.trim().toLowerCase();
  if (normalized === 'new-release') return 'NEW_RELEASE';
  if (normalized === 'cdn-change') return 'CDN_CHANGE';
  if (normalized === 'camp-launch') return 'CAMP_LAUNCH';
  throw new Error('--trigger 只允许 new-release、cdn-change 或 camp-launch');
}

function runCommand(command: string, args: string[]): Promise<void> {
  return new Promise((resolveRun, rejectRun) => {
    const child = spawn(command, args, {
      cwd: backendRoot,
      env: process.env,
      stdio: 'inherit',
    });
    child.on('error', rejectRun);
    child.on('exit', (code) => {
      if (code === 0) resolveRun();
      else rejectRun(new Error(`上传脚本退出码：${code}`));
    });
  });
}

async function appendPipelineLog(
  stateDirectory: string,
  entry: PipelineLog,
): Promise<void> {
  await mkdir(stateDirectory, { recursive: true });
  await appendFile(
    resolve(stateDirectory, 'pipeline-events.jsonl'),
    `${JSON.stringify(entry)}\n`,
    { encoding: 'utf8', mode: 0o600 },
  );
}

async function uploadRelease(sourceRoot: string): Promise<void> {
  const tsNodeBin = require.resolve('ts-node/dist/bin.js');
  await runCommand(process.execPath, [
    tsNodeBin,
    resolve(backendRoot, 'scripts/upload-public-assets.ts'),
    `--source=${sourceRoot}`,
    '--apply',
  ]);
}

export async function runVideoPrefetchEvent(
  argv = process.argv.slice(2),
): Promise<void> {
  loadEnvironment({
    path: resolve(
      process.env.TIDE_ENV_FILE || resolve(backendRoot, '.env.local'),
    ),
    quiet: true,
  });
  const apply = argv.includes('--apply');
  const trigger = parseTrigger(argument(argv, '--trigger'));
  const manifestPath = resolve(
    argument(argv, '--manifest') || defaultManifestPath,
  );
  const sourceRoot = resolve(argument(argv, '--source') || defaultSourceRoot);
  const stateDirectory = resolve(
    argument(argv, '--state-dir') ||
      process.env.VIDEO_PREFETCH_STATE_DIR ||
      defaultStateDirectory,
  );
  const manifest = await readVideoReleaseManifest(manifestPath);
  const eventId =
    argument(argv, '--event-id') ||
    (trigger === 'NEW_RELEASE' ? manifest.version : undefined);
  if (!eventId) {
    throw new Error(
      'CDN 变更和开营预热必须提供 --event-id，重复执行同一事件 ID 会自动幂等',
    );
  }
  const pipelineBase = {
    trigger,
    eventId,
    manifestVersion: manifest.version,
  };
  try {
    if (apply) {
      await appendPipelineLog(stateDirectory, {
        ...pipelineBase,
        time: new Date().toISOString(),
        status: 'PIPELINE_STARTED',
      });
    }
    let urls: string[];
    if (trigger === 'NEW_RELEASE') {
      await assertReleaseFilesExist(manifest, sourceRoot);
      urls = releaseVideoUrls(manifest);
    } else {
      const currentPaths = await currentReleaseVideoPaths(manifest);
      urls = currentPaths.map((path) =>
        publicVideoUrl(manifest.cdnBaseUrl, path),
      );
    }

    console.log(
      `${trigger}：${urls.length} 个视频，事件 ${eventId}，版本 ${manifest.version}`,
    );
    for (const url of urls) console.log(`- ${url}`);
    if (!apply) {
      console.log('当前为预检模式；确认后添加 --apply');
      return;
    }

    if (trigger === 'NEW_RELEASE') {
      await appendPipelineLog(stateDirectory, {
        ...pipelineBase,
        time: new Date().toISOString(),
        status: 'UPLOAD_STARTED',
      });
      await uploadRelease(sourceRoot);
      await appendPipelineLog(stateDirectory, {
        ...pipelineBase,
        time: new Date().toISOString(),
        status: 'UPLOAD_COMPLETE',
      });
    }

    await appendPipelineLog(stateDirectory, {
      ...pipelineBase,
      time: new Date().toISOString(),
      status: 'RANGE_CHECK_STARTED',
    });
    await verifyVideoRangeUrls(urls);
    await appendPipelineLog(stateDirectory, {
      ...pipelineBase,
      time: new Date().toISOString(),
      status: 'RANGE_CHECK_COMPLETE',
    });

    const runner = new VideoPrefetchRunner({
      provider: new AliyunVideoPrefetchProvider(),
      store: new VideoPrefetchStore(stateDirectory),
    });
    const run = await runner.run({
      trigger,
      eventId,
      manifestVersion: manifest.version,
      urls,
      domainName: new URL(manifest.cdnBaseUrl).hostname,
      l2Preload: argv.includes('--l2'),
      maxAttempts: positiveInteger(argument(argv, '--max-attempts'), 3),
      retryFailed: argv.includes('--retry-failed'),
      timeoutMilliseconds:
        positiveInteger(argument(argv, '--timeout-minutes'), 45) * 60_000,
    });
    await appendPipelineLog(stateDirectory, {
      ...pipelineBase,
      time: new Date().toISOString(),
      status: 'PREFETCH_COMPLETE',
      message: `runId=${run.runId}; taskIds=${run.taskIds.join(',')}`,
    });

    if (trigger === 'NEW_RELEASE') {
      await appendPipelineLog(stateDirectory, {
        ...pipelineBase,
        time: new Date().toISOString(),
        status: 'DATABASE_SWITCH_STARTED',
      });
      const result = await switchReleaseVideoBindings(manifest);
      await appendPipelineLog(stateDirectory, {
        ...pipelineBase,
        time: new Date().toISOString(),
        status: 'DATABASE_SWITCH_COMPLETE',
        message: `changed=${result.changed}; unchanged=${result.unchanged}`,
      });
      console.log(
        `新版本切换完成：更新 ${result.changed} 条，已是当前版本 ${result.unchanged} 条`,
      );
    } else {
      console.log(`当前 v2 预热完成：${run.taskIds.join(', ')}`);
    }
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    if (apply) {
      await appendPipelineLog(stateDirectory, {
        ...pipelineBase,
        time: new Date().toISOString(),
        status: 'FAILED',
        message,
      });
    }
    if (trigger === 'NEW_RELEASE') {
      console.error('发布失败，数据库未切换，旧版本不会删除');
    }
    throw error;
  }
}

if (require.main === module) {
  void runVideoPrefetchEvent().catch((error: unknown) => {
    const candidate =
      error && typeof error === 'object'
        ? (error as { message?: string; code?: string; requestId?: string })
        : {};
    console.error(
      `视频事件执行失败：${candidate.message || String(error)} ` +
        `[${candidate.code || 'UNKNOWN'}/${candidate.requestId || 'NONE'}]`,
    );
    process.exitCode = 1;
  });
}
