import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { config as loadEnvironment } from 'dotenv';
import { Client } from 'pg';

const backendRoot = resolve(__dirname, '..');
const defaultManifestPath = resolve(
  backendRoot,
  'config/public-videos-v2.json',
);
const defaultSiteUrl =
  'https://new-teacher-camp-mytit.tanthuy7192.chatgpt.site';

loadEnvironment({
  path: resolve(
    process.env.TIDE_ENV_FILE || resolve(backendRoot, '.env.local'),
  ),
  quiet: true,
});
loadEnvironment({
  path: resolve(
    process.env.TIDE_DATABASE_ENV_FILE ||
      resolve(backendRoot, 'database/.env.company-test'),
  ),
  quiet: true,
});

interface VideoManifest {
  cdnBaseUrl: string;
  videos: Array<{ targetPath: string; taskCodes: string[]; titleZh: string }>;
}

interface PlaybackSnapshot {
  starts: number;
  completions: number;
  stalls: number;
  failures: number;
  videoSessions: number;
  affectedSessions: number;
}

function argument(name: string): string | undefined {
  const prefix = `${name}=`;
  return process.argv
    .find((value) => value.startsWith(prefix))
    ?.slice(prefix.length);
}

function requiredArgument(name: string): string {
  const value = argument(name)?.trim();
  if (!value) throw new Error(`缺少参数：${name}`);
  return value;
}

function requiredEnvironment(...names: string[]): string {
  for (const name of names) {
    const value = process.env[name]?.trim();
    if (value) return value;
  }
  throw new Error(`缺少环境变量：${names.join(' / ')}`);
}

function positiveNumber(value: string | undefined, fallback: number): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

async function fetchWithin(
  url: string,
  options: RequestInit = {},
): Promise<Response> {
  return fetch(url, {
    ...options,
    signal: AbortSignal.timeout(15_000),
  });
}

async function verifySite(siteUrl: string, apiUrl: string) {
  const pageResponse = await fetchWithin(siteUrl);
  const html = await pageResponse.text();
  const scriptPath = html.match(/<script[^>]+src="([^"]+\.js)"/)?.[1];
  if (pageResponse.status !== 200 || !scriptPath) {
    throw new Error(`生产站点不可用：HTTP ${pageResponse.status}`);
  }
  const bundleResponse = await fetchWithin(new URL(scriptPath, siteUrl).href);
  const bundle = await bundleResponse.text();
  if (bundleResponse.status !== 200 || !bundle.includes(apiUrl)) {
    throw new Error(
      `生产 bundle 未指向活动后端：HTTP ${bundleResponse.status}`,
    );
  }
  return {
    pageStatus: pageResponse.status,
    bundleStatus: bundleResponse.status,
    usesActiveApi: true,
  };
}

async function verifyApi(apiUrl: string) {
  const paths = ['/health', '/health/ready', '/health/dependencies'];
  const statuses: Record<string, number> = {};
  for (const path of paths) {
    const response = await fetchWithin(`${apiUrl}${path}`);
    statuses[path] = response.status;
    if (response.status !== 200) {
      throw new Error(`${path} 返回 HTTP ${response.status}`);
    }
  }
  return statuses;
}

async function verifyCdn(manifest: VideoManifest) {
  const baseUrl = manifest.cdnBaseUrl.replace(/\/$/, '');
  let valid = 0;
  for (const video of manifest.videos) {
    const response = await fetchWithin(`${baseUrl}${video.targetPath}`, {
      headers: { Range: 'bytes=0-1023' },
    });
    const body = await response.arrayBuffer();
    const contentRange = response.headers.get('content-range') || '';
    const cacheControl = response.headers.get('cache-control') || '';
    if (
      response.status !== 206 ||
      body.byteLength !== 1024 ||
      !/^bytes 0-1023\/\d+$/.test(contentRange) ||
      !cacheControl.includes('max-age=31536000') ||
      !cacheControl.includes('immutable')
    ) {
      throw new Error(`CDN 校验失败：${video.titleZh} HTTP ${response.status}`);
    }
    valid += 1;
  }
  return { valid, total: manifest.videos.length };
}

function databaseClient(): Client {
  return new Client({
    host: requiredEnvironment('TIDE_DB_HOST', 'TIDE_ADMIN_DB_HOST'),
    port: Number(requiredEnvironment('TIDE_DB_PORT', 'TIDE_ADMIN_DB_PORT')),
    user: requiredEnvironment('TIDE_DB_USER', 'TIDE_ADMIN_DB_USER'),
    password: requiredEnvironment('TIDE_DB_PASSWORD', 'TIDE_ADMIN_DB_PASSWORD'),
    database: requiredEnvironment('TIDE_DB_NAME', 'TIDE_ADMIN_DB_NAME'),
    ssl:
      (process.env.TIDE_DB_SSLMODE ||
        process.env.TIDE_ADMIN_DB_SSLMODE ||
        'disable') === 'disable'
        ? false
        : { rejectUnauthorized: false },
  });
}

async function verifyDatabase(manifest: VideoManifest, since: Date) {
  const taskCodes = [
    ...new Set(manifest.videos.flatMap((video) => video.taskCodes)),
  ];
  const client = databaseClient();
  await client.connect();
  try {
    const references = await client.query<{
      v1Refs: string;
      v2Refs: string;
    }>(
      `
        SELECT
          count(*) FILTER (
            WHERE COALESCE(
              step.config->>'assetUrl',
              step.config#>>'{referenceVideo,assetUrl}',
              ''
            ) LIKE '%/v1/%'
          ) AS "v1Refs",
          count(*) FILTER (
            WHERE COALESCE(
              step.config->>'assetUrl',
              step.config#>>'{referenceVideo,assetUrl}',
              ''
            ) LIKE '%/v2/%'
          ) AS "v2Refs"
        FROM tide.task_step_definitions step
        JOIN tide.task_execution_versions execution
          ON execution.id = step.execution_version_id
        WHERE execution.task_code = ANY($1::varchar[])
          AND (
            step.config ? 'assetUrl'
            OR step.config ? 'referenceVideo'
          )
      `,
      [taskCodes],
    );
    const v1Refs = Number(references.rows[0]?.v1Refs || 0);
    const v2Refs = Number(references.rows[0]?.v2Refs || 0);
    if (v1Refs !== 0 || v2Refs < manifest.videos.length) {
      throw new Error(`数据库视频引用异常：v1=${v1Refs}，v2=${v2Refs}`);
    }

    const playbackResult = await client.query<{
      starts: string;
      completions: string;
      stalls: string;
      failures: string;
      videoSessions: string;
      affectedSessions: string;
    }>(
      `
        SELECT
          count(*) FILTER (
            WHERE event_name IN ('VIDEO_PLAYED', 'VIDEO_RESUMED')
          ) AS starts,
          count(*) FILTER (
            WHERE event_name = 'VIDEO_COMPLETED'
          ) AS completions,
          count(*) FILTER (
            WHERE event_name = 'VIDEO_STALLED'
          ) AS stalls,
          count(*) FILTER (
            WHERE event_name = 'VIDEO_FAILED'
          ) AS failures,
          count(DISTINCT session_id) FILTER (
            WHERE event_name LIKE 'VIDEO_%'
          ) AS "videoSessions",
          count(DISTINCT session_id) FILTER (
            WHERE event_name IN ('VIDEO_STALLED', 'VIDEO_FAILED')
          ) AS "affectedSessions"
        FROM tide.app_events
        WHERE occurred_at >= $1
      `,
      [since.toISOString()],
    );
    const row = playbackResult.rows[0];
    const playback: PlaybackSnapshot = {
      starts: Number(row?.starts || 0),
      completions: Number(row?.completions || 0),
      stalls: Number(row?.stalls || 0),
      failures: Number(row?.failures || 0),
      videoSessions: Number(row?.videoSessions || 0),
      affectedSessions: Number(row?.affectedSessions || 0),
    };
    const affectedRate =
      playback.videoSessions > 0
        ? playback.affectedSessions / playback.videoSessions
        : 0;
    if (
      playback.affectedSessions >= 3 &&
      affectedRate > positiveNumber(argument('--max-affected-rate'), 0.3)
    ) {
      throw new Error(
        `视频异常会话占比过高：${playback.affectedSessions}/${playback.videoSessions}`,
      );
    }
    return {
      references: { v1: v1Refs, v2: v2Refs },
      playback: {
        ...playback,
        affectedSessionRate: Number(affectedRate.toFixed(4)),
      },
    };
  } finally {
    await client.end();
  }
}

async function main(): Promise<void> {
  const since = new Date(requiredArgument('--since'));
  if (!Number.isFinite(since.getTime())) {
    throw new Error('--since 必须是合法 ISO 时间');
  }
  const minimumHours = positiveNumber(argument('--minimum-hours'), 24);
  const observedHours = (Date.now() - since.getTime()) / 3_600_000;
  if (observedHours < minimumHours) {
    throw new Error(
      `观察期未满：${observedHours.toFixed(2)}/${minimumHours} 小时`,
    );
  }

  const manifestPath = resolve(argument('--manifest') || defaultManifestPath);
  const manifest = JSON.parse(
    await readFile(manifestPath, 'utf8'),
  ) as VideoManifest;
  const siteUrl = (
    process.env.TIDE_PRODUCTION_SITE_URL || defaultSiteUrl
  ).replace(/\/$/, '');
  const apiUrl = requiredEnvironment('TIDE_INTERNAL_PUBLIC_API_URL').replace(
    /\/$/,
    '',
  );

  const report = {
    checkedAt: new Date().toISOString(),
    since: since.toISOString(),
    observedHours: Number(observedHours.toFixed(2)),
    site: await verifySite(siteUrl, apiUrl),
    api: await verifyApi(apiUrl),
    cdn: await verifyCdn(manifest),
    database: await verifyDatabase(manifest, since),
  };
  console.log(JSON.stringify(report, null, 2));
}

void main().catch((error: unknown) => {
  console.error(
    `v2 发布验收失败：${error instanceof Error ? error.message : String(error)}`,
  );
  process.exitCode = 1;
});
