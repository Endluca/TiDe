import { spawn } from 'node:child_process';
import {
  access,
  mkdir,
  open,
  readFile,
  readdir,
  rename,
  rm,
  stat,
  writeFile,
} from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const scriptDirectory = dirname(fileURLToPath(import.meta.url));
const backendRoot = resolve(scriptDirectory, '..');
const defaultManifestPath = resolve(
  backendRoot,
  'config/public-videos-v2.json',
);
const defaultWorkDirectory = resolve(backendRoot, '../.video-v2-media');
const rangeChunkBytes = 16 * 1024 * 1024;

function argument(name) {
  const prefix = `${name}=`;
  return process.argv
    .find((value) => value.startsWith(prefix))
    ?.slice(prefix.length);
}

function hasFlag(name) {
  return process.argv.includes(name);
}

function positiveInteger(value, fallback) {
  const number = Number(value);
  return Number.isInteger(number) && number > 0 ? number : fallback;
}

async function exists(path) {
  try {
    await access(path);
    return true;
  } catch {
    return false;
  }
}

function run(command, args, options = {}) {
  return new Promise((resolveRun, rejectRun) => {
    const child = spawn(command, args, {
      stdio: options.capture ? ['ignore', 'pipe', 'pipe'] : 'inherit',
      ...options,
    });
    let stdout = '';
    let stderr = '';
    child.stdout?.on('data', (chunk) => {
      stdout += chunk;
    });
    child.stderr?.on('data', (chunk) => {
      stderr += chunk;
    });
    child.on('error', rejectRun);
    child.on('exit', (code) => {
      if (code === 0) {
        resolveRun(stdout);
      } else {
        rejectRun(
          new Error(
            `${command} exited with ${code}${stderr ? `\n${stderr}` : ''}`,
          ),
        );
      }
    });
  });
}

async function runPool(items, concurrency, operation) {
  const queue = [...items];
  const failures = [];
  const workers = Array.from(
    { length: Math.min(concurrency, queue.length) },
    async () => {
      while (queue.length) {
        const item = queue.shift();
        if (item === undefined) continue;
        try {
          await operation(item);
        } catch (error) {
          failures.push({ item, error });
        }
      }
    },
  );
  await Promise.all(workers);
  if (failures.length > 0) {
    throw new AggregateError(
      failures.map(({ error }) => error),
      `${failures.length} 个并行任务失败，可重新执行以续传`,
    );
  }
}

async function remoteSize(url) {
  let lastError;
  for (let attempt = 1; attempt <= 5; attempt += 1) {
    try {
      const response = await fetch(url, {
        headers: { Range: 'bytes=0-0' },
        signal: AbortSignal.timeout(30_000),
      });
      if (response.status !== 206) {
        throw new Error(
          `Range probe failed for ${url}: HTTP ${response.status}`,
        );
      }
      const contentRange = response.headers.get('content-range') || '';
      const total = Number(contentRange.match(/\/(\d+)$/)?.[1]);
      if (!Number.isSafeInteger(total) || total <= 0) {
        throw new Error(`Missing remote size for ${url}`);
      }
      return total;
    } catch (error) {
      lastError = error;
      if (attempt < 5) {
        await new Promise((resolveDelay) =>
          setTimeout(resolveDelay, 500 * 2 ** (attempt - 1)),
        );
      }
    }
  }
  throw lastError;
}

async function downloadRange(url, start, end, path) {
  const expected = end - start + 1;
  if (await exists(path)) {
    const current = await stat(path);
    if (current.size === expected) return;
    await rm(path);
  }
  for (let attempt = 1; attempt <= 5; attempt += 1) {
    try {
      const response = await fetch(url, {
        headers: { Range: `bytes=${start}-${end}` },
        signal: AbortSignal.timeout(120_000),
      });
      if (response.status !== 206) {
        throw new Error(`HTTP ${response.status}`);
      }
      const body = Buffer.from(await response.arrayBuffer());
      if (body.length !== expected) {
        throw new Error(`expected ${expected} bytes, received ${body.length}`);
      }
      await writeFile(path, body);
      return;
    } catch (error) {
      if (attempt === 5) throw error;
      await new Promise((resolveDelay) =>
        setTimeout(resolveDelay, 500 * 2 ** (attempt - 1)),
      );
    }
  }
}

async function appendFile(targetHandle, sourcePath) {
  const sourceHandle = await open(sourcePath, 'r');
  try {
    const buffer = Buffer.allocUnsafe(1024 * 1024);
    let position = 0;
    while (true) {
      const { bytesRead } = await sourceHandle.read(
        buffer,
        0,
        buffer.length,
        position,
      );
      if (bytesRead === 0) break;
      await targetHandle.write(buffer.subarray(0, bytesRead));
      position += bytesRead;
    }
  } finally {
    await sourceHandle.close();
  }
}

async function downloadVideo(video, sourceRoot, sourceBaseUrl, concurrency) {
  const target = resolve(sourceRoot, `.${video.sourcePath}`);
  const url = `${sourceBaseUrl.replace(/\/$/, '')}${video.sourcePath}`;
  const total = await remoteSize(url);
  await mkdir(dirname(target), { recursive: true });

  if (await exists(target)) {
    const current = await stat(target);
    if (current.size === total) {
      console.log(`已下载：${video.titleZh}`);
      return target;
    }
    if (current.size > total) {
      throw new Error(`Local source is larger than remote: ${target}`);
    }
  }

  const prefix = `${target}.prefix`;
  if (!(await exists(prefix)) && (await exists(target))) {
    await rename(target, prefix);
  }
  const prefixBytes = (await exists(prefix)) ? (await stat(prefix)).size : 0;
  const partsDirectory = `${target}.parts`;
  await mkdir(partsDirectory, { recursive: true });
  const ranges = [];
  for (let start = prefixBytes; start < total; start += rangeChunkBytes) {
    const end = Math.min(total - 1, start + rangeChunkBytes - 1);
    ranges.push({
      start,
      end,
      path: resolve(partsDirectory, `${start}-${end}.part`),
    });
  }

  console.log(`下载：${video.titleZh} ${(total / 1024 / 1024).toFixed(1)} MiB`);
  let completed = 0;
  await runPool(ranges, concurrency, async (range) => {
    await downloadRange(url, range.start, range.end, range.path);
    completed += 1;
    if (
      completed === ranges.length ||
      completed % Math.max(1, Math.ceil(ranges.length / 4)) === 0
    ) {
      console.log(`  ${video.titleZh}：${completed}/${ranges.length} 个分片`);
    }
  });

  const assembly = `${target}.assembling`;
  const assemblyHandle = await open(assembly, 'w');
  try {
    if (await exists(prefix)) await appendFile(assemblyHandle, prefix);
    for (const range of ranges) {
      await appendFile(assemblyHandle, range.path);
    }
  } finally {
    await assemblyHandle.close();
  }
  const assembled = await stat(assembly);
  if (assembled.size !== total) {
    throw new Error(
      `Assembled size mismatch for ${video.sourcePath}: ${assembled.size}/${total}`,
    );
  }
  await rename(assembly, target);
  await rm(prefix, { force: true });
  await rm(partsDirectory, { recursive: true, force: true });
  console.log(`下载完成：${video.titleZh}`);
  return target;
}

async function probe(path) {
  const output = await run(
    'ffprobe',
    [
      '-v',
      'error',
      '-show_entries',
      'format=duration,size,bit_rate:stream=codec_name,codec_type,width,height,pix_fmt',
      '-of',
      'json',
      path,
    ],
    { capture: true },
  );
  return JSON.parse(output);
}

async function hasFastStart(path) {
  const handle = await open(path, 'r');
  try {
    const size = Math.min(2 * 1024 * 1024, (await handle.stat()).size);
    const buffer = Buffer.allocUnsafe(size);
    await handle.read(buffer, 0, size, 0);
    return buffer.indexOf(Buffer.from('moov')) >= 0;
  } finally {
    await handle.close();
  }
}

async function verifyOutput(video, sourceProbe, target) {
  const targetProbe = await probe(target);
  const sourceDuration = Number(sourceProbe.format?.duration);
  const targetDuration = Number(targetProbe.format?.duration);
  if (
    !Number.isFinite(targetDuration) ||
    Math.abs(sourceDuration - targetDuration) > 1 ||
    Math.round(targetDuration) !== video.durationSeconds
  ) {
    throw new Error(
      `Duration mismatch for ${video.targetPath}: source=${sourceDuration}, target=${targetDuration}, configured=${video.durationSeconds}`,
    );
  }
  const videoStream = targetProbe.streams?.find(
    (stream) => stream.codec_type === 'video',
  );
  if (
    videoStream?.codec_name !== 'h264' ||
    videoStream?.pix_fmt !== 'yuv420p'
  ) {
    throw new Error(`Unsupported video output for ${video.targetPath}`);
  }
  const audioStream = targetProbe.streams?.find(
    (stream) => stream.codec_type === 'audio',
  );
  if (audioStream && audioStream.codec_name !== 'aac') {
    throw new Error(`Unsupported audio output for ${video.targetPath}`);
  }
  if (video.processing === 'transcode' && Number(videoStream.height) > 720) {
    throw new Error(`Output is taller than 720p for ${video.targetPath}`);
  }
  if (!(await hasFastStart(target))) {
    throw new Error(`MP4 faststart check failed for ${video.targetPath}`);
  }
  const bitRate = Number(targetProbe.format?.bit_rate);
  if (video.processing === 'transcode' && bitRate > 1_800_000) {
    throw new Error(
      `Output bitrate is too high for ${video.targetPath}: ${bitRate}`,
    );
  }
  return {
    durationSeconds: targetDuration,
    sizeBytes: Number(targetProbe.format?.size),
    bitRate,
    width: Number(videoStream.width),
    height: Number(videoStream.height),
    codec: videoStream.codec_name,
    audioCodec: audioStream?.codec_name || null,
    fastStart: true,
  };
}

async function processVideo(video, sourceRoot, outputRoot) {
  const source = resolve(sourceRoot, `.${video.sourcePath}`);
  const target = resolve(outputRoot, `.${video.targetPath}`);
  const temporaryTarget = `${target}.tmp.mp4`;
  await mkdir(dirname(target), { recursive: true });
  const sourceProbe = await probe(source);
  const sourceSizeBytes = Number(sourceProbe.format?.size);

  if (await exists(target)) {
    try {
      const verified = await verifyOutput(video, sourceProbe, target);
      console.log(`已处理：${video.titleZh}`);
      return { ...verified, sourceSizeBytes };
    } catch {
      await rm(target);
    }
  }

  const common = [
    '-hide_banner',
    '-loglevel',
    'error',
    '-y',
    '-i',
    source,
    '-map',
    '0:v:0',
    '-map',
    '0:a:0?',
    '-sn',
    '-dn',
  ];
  const processing =
    video.processing === 'remux'
      ? ['-c', 'copy']
      : [
          '-vf',
          'scale=-2:720:flags=lanczos',
          '-c:v',
          'libx264',
          '-preset',
          'veryfast',
          '-crf',
          '23',
          '-maxrate',
          '1500k',
          '-bufsize',
          '3000k',
          '-profile:v',
          'high',
          '-pix_fmt',
          'yuv420p',
          '-force_key_frames',
          'expr:gte(t,n_forced*2)',
          '-c:a',
          'aac',
          '-b:a',
          '128k',
        ];
  console.log(
    `${video.processing === 'remux' ? '整理' : '转码'}：${video.titleZh}`,
  );
  await run('ffmpeg', [
    ...common,
    ...processing,
    '-movflags',
    '+faststart',
    temporaryTarget,
  ]);
  await rename(temporaryTarget, target);
  return {
    ...(await verifyOutput(video, sourceProbe, target)),
    sourceSizeBytes,
  };
}

async function main() {
  const manifestPath = resolve(argument('--manifest') || defaultManifestPath);
  const workDirectory = resolve(argument('--work-dir') || defaultWorkDirectory);
  const downloadConcurrency = positiveInteger(
    argument('--download-concurrency'),
    8,
  );
  const videoConcurrency = positiveInteger(argument('--video-concurrency'), 3);
  const manifest = JSON.parse(await readFile(manifestPath, 'utf8'));
  const requestedTaskCodes = new Set(
    (argument('--task-codes') || '')
      .split(',')
      .map((code) => code.trim())
      .filter(Boolean),
  );
  const videos = requestedTaskCodes.size
    ? manifest.videos.filter((video) =>
        video.taskCodes?.some((code) => requestedTaskCodes.has(code)),
      )
    : manifest.videos;
  if (!videos.length) {
    throw new Error(
      `No videos matched --task-codes=${[...requestedTaskCodes].join(',')}`,
    );
  }
  const sourceBaseUrl = argument('--source-base-url') || manifest.sourceBaseUrl;
  const sourceRoot = resolve(workDirectory, 'source');
  const outputRoot = resolve(workDirectory, 'output');
  await mkdir(sourceRoot, { recursive: true });
  await mkdir(outputRoot, { recursive: true });

  await run('ffmpeg', ['-version'], { capture: true });
  await run('ffprobe', ['-version'], { capture: true });

  if (!hasFlag('--process-only')) {
    await runPool(videos, videoConcurrency, (video) =>
      downloadVideo(video, sourceRoot, sourceBaseUrl, downloadConcurrency),
    );
  }
  if (hasFlag('--download-only')) return;

  const processVideos = [];
  for (const video of videos) {
    const source = resolve(sourceRoot, `.${video.sourcePath}`);
    if (!(await exists(source)) && hasFlag('--skip-missing')) {
      console.log(`尚未下载，跳过处理：${video.titleZh}`);
      continue;
    }
    processVideos.push(video);
  }
  if (processVideos.length === 0) return;
  const report = [];
  for (const video of processVideos) {
    const output = await processVideo(video, sourceRoot, outputRoot);
    report.push({ ...video, output });
  }
  await writeFile(
    resolve(workDirectory, 'release-report.json'),
    `${JSON.stringify(
      {
        generatedAt: new Date().toISOString(),
        manifestVersion: manifest.version,
        videos: report,
      },
      null,
      2,
    )}\n`,
  );
  const sourceBytes = report.reduce(
    (sum, item) => sum + Number(item.output?.sourceSizeBytes || 0),
    0,
  );
  const outputBytes = report.reduce(
    (sum, item) => sum + Number(item.output?.sizeBytes || 0),
    0,
  );
  console.log(
    `v2 完成：${report.length} 个视频，源 ${(sourceBytes / 1024 / 1024).toFixed(1)} MiB，输出 ${(outputBytes / 1024 / 1024).toFixed(1)} MiB`,
  );
}

main().catch(async (error) => {
  console.error(error);
  try {
    const workDirectory = resolve(
      argument('--work-dir') || defaultWorkDirectory,
    );
    const leftovers = await readdir(workDirectory).catch(() => []);
    if (leftovers.length === 0) {
      await rm(workDirectory, { recursive: true });
    }
  } catch {
    // Preserve partial media work for a resumable retry.
  }
  process.exitCode = 1;
});
