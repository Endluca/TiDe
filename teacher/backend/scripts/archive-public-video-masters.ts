import OSS from 'ali-oss';
import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { config as loadEnvironment } from 'dotenv';

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
  sourceBaseUrl: string;
  videos: Array<{ sourcePath: string; titleZh: string }>;
}

function argument(name: string): string | undefined {
  const prefix = `${name}=`;
  return process.argv
    .find((value) => value.startsWith(prefix))
    ?.slice(prefix.length);
}

function requiredEnvironment(publicName: string, privateName: string): string {
  const value =
    process.env[publicName]?.trim() || process.env[privateName]?.trim();
  if (!value) throw new Error(`缺少 OSS 配置：${publicName}/${privateName}`);
  return value;
}

function header(headers: object, name: string): string | number | undefined {
  const entries = headers as Record<string, unknown>;
  const value = entries[name] ?? entries[name.toLowerCase()];
  return typeof value === 'string' || typeof value === 'number'
    ? value
    : undefined;
}

function objectKey(path: string): string {
  return path.replace(/^\/+/, '');
}

function anonymousUrl(baseUrl: string, key: string): string {
  return `${baseUrl.replace(/\/$/, '')}/${key
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

async function verifyPrivate(url: string): Promise<void> {
  const response = await fetch(url, {
    headers: { Range: 'bytes=0-0' },
    redirect: 'manual',
    signal: AbortSignal.timeout(10_000),
  });
  if (![401, 403, 404].includes(response.status)) {
    throw new Error(`私有归档仍可匿名读取：HTTP ${response.status} ${url}`);
  }
}

async function main(): Promise<void> {
  const apply = process.argv.includes('--apply');
  const manifestPath = resolve(argument('--manifest') || defaultManifestPath);
  const archivePrefix = (argument('--prefix') || 'private-masters')
    .replace(/^\/+|\/+$/g, '')
    .trim();
  if (!archivePrefix || archivePrefix.split('/').includes('..')) {
    throw new Error('归档前缀无效');
  }
  const manifest = JSON.parse(
    await readFile(manifestPath, 'utf8'),
  ) as VideoManifest;

  console.log(
    `私有母版归档：${manifest.videos.length} 个，对象前缀 ${archivePrefix}/`,
  );
  if (!apply) {
    console.log('当前为预检模式；确认后添加 --apply 执行服务端复制');
    return;
  }

  const region = requiredEnvironment('PUBLIC_ASSET_OSS_REGION', 'OSS_REGION');
  const bucket = requiredEnvironment('PUBLIC_ASSET_OSS_BUCKET', 'OSS_BUCKET');
  const endpoint = normalizeEndpoint(
    requiredEnvironment('PUBLIC_ASSET_OSS_ENDPOINT', 'OSS_ENDPOINT'),
    bucket,
  );
  const client = new OSS({
    region,
    endpoint,
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

  let copied = 0;
  for (const video of manifest.videos) {
    const sourceKey = objectKey(video.sourcePath);
    const archiveKey = `${archivePrefix}/${sourceKey}`;
    const source = await client.head(sourceKey);
    const sourceLength = Number(header(source.res.headers, 'content-length'));
    const sourceEtag = String(
      header(source.res.headers, 'etag') || '',
    ).replaceAll('"', '');
    let alreadyArchived = false;
    try {
      const archived = await client.head(archiveKey);
      const archivedLength = Number(
        header(archived.res.headers, 'content-length'),
      );
      const archivedSourceEtag = String(
        header(archived.res.headers, 'x-oss-meta-source-etag') || '',
      );
      alreadyArchived =
        archivedLength === sourceLength && archivedSourceEtag === sourceEtag;
    } catch (error) {
      const candidate = error as { status?: number; code?: string };
      if (candidate.status !== 404 && candidate.code !== 'NoSuchKey') {
        throw error;
      }
    }

    if (!alreadyArchived) {
      await client.copy(archiveKey, sourceKey, {
        headers: {
          'x-oss-metadata-directive': 'REPLACE',
          'x-oss-object-acl': 'private',
          'Content-Type': 'video/mp4',
          'Cache-Control': 'private, no-store',
          'x-oss-meta-source-etag': sourceEtag,
        },
      });
      copied += 1;
    }
    await client.putACL(archiveKey, 'private');
    await verifyPrivate(anonymousUrl(manifest.sourceBaseUrl, archiveKey));
    console.log(
      `${alreadyArchived ? '已存在' : '已归档'}：${video.titleZh} → ${archiveKey}`,
    );
  }
  console.log(
    `私有母版归档完成：新增/更新 ${copied}，校验 ${manifest.videos.length}`,
  );
}

void main().catch((error: unknown) => {
  const candidate =
    error && typeof error === 'object'
      ? (error as {
          message?: string;
          code?: string;
          status?: number;
          requestId?: string;
        })
      : {};
  console.error(
    `私有母版归档失败：${candidate.message || String(error)} ` +
      `[${candidate.code || 'UNKNOWN'}/${candidate.status || 'UNKNOWN'}/${candidate.requestId || 'NONE'}]`,
  );
  process.exitCode = 1;
});
