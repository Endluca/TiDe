import CdnClient, {
  DescribeRefreshTasksRequest,
  RefreshObjectCachesRequest,
} from '@alicloud/cdn20180510';
import { Config } from '@alicloud/openapi-client';
import OSS from 'ali-oss';
import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { config as loadEnvironment } from 'dotenv';
import { AliyunVideoPrefetchProvider } from '../src/video-delivery/aliyun-video-prefetch.provider';
import { VideoPrefetchRunner } from '../src/video-delivery/video-prefetch.runner';
import { VideoPrefetchStore } from '../src/video-delivery/video-prefetch.store';

const backendRoot = resolve(__dirname, '..');
const defaultManifestPath = resolve(
  backendRoot,
  'config/public-videos-v2.json',
);
loadEnvironment({
  path: resolve(
    process.env.TIDE_ENV_FILE || resolve(backendRoot, '.env.local'),
  ),
  quiet: true,
});

interface VideoManifest {
  version: string;
  cdnBaseUrl: string;
  videos: Array<{
    sourcePath: string;
    targetPath: string;
    titleZh: string;
  }>;
}

function argument(name: string): string | undefined {
  const prefix = `${name}=`;
  return process.argv
    .find((value) => value.startsWith(prefix))
    ?.slice(prefix.length);
}

function requiredEnvironment(publicName: string, privateName?: string): string {
  const value =
    process.env[publicName]?.trim() ||
    (privateName ? process.env[privateName]?.trim() : undefined);
  if (!value) {
    throw new Error(
      `缺少配置：${privateName ? `${publicName}/${privateName}` : publicName}`,
    );
  }
  return value;
}

function objectKey(path: string): string {
  return path.replace(/^\/+/, '');
}

function publicUrl(baseUrl: string, path: string): string {
  return `${baseUrl.replace(/\/$/, '')}/${objectKey(path)
    .split('/')
    .map(encodeURIComponent)
    .join('/')}`;
}

function normalizeEndpoint(endpoint: string, bucket: string): string {
  const url = new URL(endpoint);
  const bucketPrefix = `${bucket}.`;
  if (
    url.hostname.startsWith(bucketPrefix) &&
    url.hostname.slice(bucketPrefix.length).startsWith('oss-') &&
    url.hostname.endsWith('.aliyuncs.com')
  ) {
    url.hostname = url.hostname.slice(bucketPrefix.length);
  }
  return url.toString().replace(/\/$/, '');
}

async function verifyVideo(url: string): Promise<void> {
  const response = await fetch(url, {
    headers: { Range: 'bytes=0-1023' },
    redirect: 'manual',
    signal: AbortSignal.timeout(15_000),
  });
  if (
    response.status !== 206 ||
    !/^bytes 0-1023\/\d+$/.test(response.headers.get('content-range') || '')
  ) {
    throw new Error(`v2 CDN 校验失败：HTTP ${response.status} ${url}`);
  }
}

async function waitForRefresh(
  client: CdnClient,
  taskIds: string[],
  domainName: string,
): Promise<void> {
  const deadline = Date.now() + 30 * 60_000;
  while (Date.now() < deadline) {
    const statuses = await Promise.all(
      taskIds.map(async (taskId) => {
        const response = await client.describeRefreshTasks(
          new DescribeRefreshTasksRequest({
            taskId,
            domainName,
            objectType: 'file',
            pageSize: 100,
          }),
        );
        const tasks = response.body?.tasks?.CDNTask || [];
        if (tasks.some((task) => task.status === 'Failed')) {
          throw new Error(`CDN v1 清理失败：任务 ${taskId}`);
        }
        return (
          tasks.length > 0 && tasks.every((task) => task.status === 'Complete')
        );
      }),
    );
    if (statuses.every(Boolean)) return;
    await new Promise((resolveDelay) => setTimeout(resolveDelay, 15_000));
  }
  throw new Error('CDN v1 清理等待超过 30 分钟');
}

async function main(): Promise<void> {
  const apply = process.argv.includes('--apply');
  const confirmedLive = process.argv.includes('--confirm-v2-live');
  const manifestPath = resolve(argument('--manifest') || defaultManifestPath);
  const archivePrefix = (
    argument('--archive-prefix') || 'private-masters'
  ).replace(/^\/+|\/+$/g, '');
  const notBeforeValue = argument('--not-before');
  const manifest = JSON.parse(
    await readFile(manifestPath, 'utf8'),
  ) as VideoManifest;
  const stateDirectory = resolve(
    argument('--state-dir') ||
      process.env.VIDEO_PREFETCH_STATE_DIR ||
      resolve(backendRoot, 'storage/video-prefetch-runs'),
  );
  console.log(
    `v1 清理清单：${manifest.videos.length} 个公开对象；私有母版不会删除`,
  );
  if (!apply) {
    console.log(
      '当前为预检模式；观察满 24 小时后添加 --apply、--confirm-v2-live 和 --not-before=<ISO 时间>',
    );
    return;
  }
  if (!confirmedLive) throw new Error('必须显式传入 --confirm-v2-live');
  const notBefore = new Date(notBeforeValue || '');
  if (!notBeforeValue || Number.isNaN(notBefore.getTime())) {
    throw new Error('必须提供有效的 --not-before=<ISO 时间>');
  }
  if (Date.now() < notBefore.getTime()) {
    throw new Error(`尚未到清理时间：${notBefore.toISOString()}`);
  }
  const prefetchStore = new VideoPrefetchStore(stateDirectory);
  if (!(await prefetchStore.hasSuccessfulNewRelease(manifest.version))) {
    throw new Error(
      `未找到 ${manifest.version} 的成功发布预热记录，禁止删除旧版本`,
    );
  }

  const region = requiredEnvironment('PUBLIC_ASSET_OSS_REGION', 'OSS_REGION');
  const bucket = requiredEnvironment('PUBLIC_ASSET_OSS_BUCKET', 'OSS_BUCKET');
  const oss = new OSS({
    region,
    endpoint: normalizeEndpoint(
      requiredEnvironment('PUBLIC_ASSET_OSS_ENDPOINT', 'OSS_ENDPOINT'),
      bucket,
    ),
    bucket,
    accessKeyId: requiredEnvironment(
      'PUBLIC_ASSET_OSS_ACCESS_KEY_ID',
      'OSS_ACCESS_KEY_ID',
    ),
    accessKeySecret: requiredEnvironment(
      'PUBLIC_ASSET_OSS_ACCESS_KEY_SECRET',
      'OSS_ACCESS_KEY_SECRET',
    ),
    authorizationV4: true,
    timeout: 60_000,
  });
  const cdn = new CdnClient(
    new Config({
      accessKeyId: requiredEnvironment('CDN_ACCESS_KEY_ID'),
      accessKeySecret: requiredEnvironment('CDN_ACCESS_KEY_SECRET'),
      regionId: process.env.CDN_REGION_ID?.trim() || 'ap-southeast-1',
      endpoint:
        process.env.CDN_API_ENDPOINT?.trim() ||
        'cdn.ap-southeast-1.aliyuncs.com',
    }),
  );
  const domainName = new URL(manifest.cdnBaseUrl).hostname;

  await cdn.describeRefreshTasks(
    new DescribeRefreshTasksRequest({
      domainName,
      objectType: 'file',
      pageSize: 1,
    }),
  );
  for (const video of manifest.videos) {
    await oss.head(`${archivePrefix}/${objectKey(video.sourcePath)}`);
    await verifyVideo(publicUrl(manifest.cdnBaseUrl, video.targetPath));
  }

  const deleted: string[] = [];
  try {
    for (const video of manifest.videos) {
      const key = objectKey(video.sourcePath);
      await oss.delete(key);
      deleted.push(key);
      console.log(`已删除公开 v1：${key}`);
    }
    const response = await cdn.refreshObjectCaches(
      new RefreshObjectCachesRequest({
        objectPath: manifest.videos
          .map((video) => publicUrl(manifest.cdnBaseUrl, video.sourcePath))
          .join('\n'),
        objectType: 'File',
      }),
    );
    const taskIds = (response.body?.refreshTaskId || '')
      .split(',')
      .map((taskId) => taskId.trim())
      .filter(Boolean);
    if (!taskIds.length) throw new Error('阿里云未返回 CDN 刷新任务 ID');
    console.log(`已提交 CDN v1 清理：${taskIds.join(', ')}`);
    await waitForRefresh(cdn, taskIds, domainName);
    const currentUrls = manifest.videos.map((video) =>
      publicUrl(manifest.cdnBaseUrl, video.targetPath),
    );
    const rewarm = await new VideoPrefetchRunner({
      provider: new AliyunVideoPrefetchProvider(cdn),
      store: prefetchStore,
    }).run({
      trigger: 'CDN_CHANGE',
      eventId: `v1-cleanup:${manifest.version}:${taskIds.sort().join(',')}`,
      manifestVersion: manifest.version,
      urls: currentUrls,
      domainName,
      retryFailed: process.argv.includes('--retry-failed'),
    });
    console.log(`v2 自动回热完成：${rewarm.taskIds.join(', ')}`);
  } catch (error) {
    for (const key of deleted) {
      await oss.copy(key, `${archivePrefix}/${key}`, {
        headers: {
          'x-oss-metadata-directive': 'REPLACE',
          'x-oss-object-acl': 'public-read',
          'Content-Type': 'video/mp4',
          'Cache-Control': 'public, max-age=3600',
        },
      });
    }
    if (deleted.length) {
      console.error(`清理失败，已从私有母版恢复 ${deleted.length} 个 v1 对象`);
      try {
        const response = await cdn.refreshObjectCaches(
          new RefreshObjectCachesRequest({
            objectPath: manifest.videos
              .filter((video) => deleted.includes(objectKey(video.sourcePath)))
              .map((video) => publicUrl(manifest.cdnBaseUrl, video.sourcePath))
              .join('\n'),
            objectType: 'File',
          }),
        );
        const restoreTaskIds = (response.body?.refreshTaskId || '')
          .split(',')
          .map((taskId) => taskId.trim())
          .filter(Boolean);
        if (restoreTaskIds.length) {
          await waitForRefresh(cdn, restoreTaskIds, domainName);
          console.error('已刷新恢复后的 v1 CDN 缓存');
        }
      } catch (refreshError) {
        console.error(
          `v1 对象已恢复，但 CDN 刷新失败：${
            refreshError instanceof Error
              ? refreshError.message
              : String(refreshError)
          }`,
        );
      }
    }
    throw error;
  }
  console.log('公开视频 v1 与 CDN 缓存清理完成；私有母版已保留');
}

void main().catch((error: unknown) => {
  const candidate =
    error && typeof error === 'object'
      ? (error as { message?: string; code?: string; requestId?: string })
      : {};
  console.error(
    `v1 清理失败：${candidate.message || String(error)} ` +
      `[${candidate.code || 'UNKNOWN'}/${candidate.requestId || 'NONE'}]`,
  );
  process.exitCode = 1;
});
