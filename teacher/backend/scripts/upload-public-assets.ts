import OSS from 'ali-oss';
import { createHash } from 'node:crypto';
import { createReadStream } from 'node:fs';
import {
  mkdir,
  readFile,
  readdir,
  rename,
  stat,
  unlink,
  writeFile,
} from 'node:fs/promises';
import { extname, relative, resolve, sep } from 'node:path';
import { config as loadEnvironment } from 'dotenv';

const backendRoot = resolve(__dirname, '..');
const checkpointRoot = resolve(backendRoot, 'storage/upload-checkpoints');
const multipartThreshold = 100 * 1024 * 1024;
const multipartPartSize = 10 * 1024 * 1024;
const ossMultipartTimeout = 10 * 60 * 1000;
loadEnvironment({
  path: resolve(
    process.env.TIDE_ENV_FILE || resolve(backendRoot, '.env.local'),
  ),
  quiet: true,
});

const allowedExtensions = new Set([
  '.jpg',
  '.jpeg',
  '.png',
  '.webp',
  '.svg',
  '.pdf',
  '.mp4',
  '.webm',
  '.mov',
  '.m4v',
  '.mp3',
  '.wav',
]);

const mimeTypes: Record<string, string> = {
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.png': 'image/png',
  '.webp': 'image/webp',
  '.svg': 'image/svg+xml',
  '.pdf': 'application/pdf',
  '.mp4': 'video/mp4',
  '.webm': 'video/webm',
  '.mov': 'video/quicktime',
  '.m4v': 'video/x-m4v',
  '.mp3': 'audio/mpeg',
  '.wav': 'audio/wav',
};

interface LocalAsset {
  absolutePath: string;
  objectKey: string;
  extension: string;
  sizeBytes: number;
  sha256: string;
}

interface OssSettings {
  region: string;
  endpoint: string;
  bucket: string;
  accessKeyId: string;
  accessKeySecret: string;
  publicBaseUrl: string;
  cdnBaseUrl?: string;
}

interface MultipartCheckpoint {
  file: string;
  name: string;
  fileSize: number;
  partSize: number;
  uploadId: string;
  doneParts: Array<{ number: number; etag: string }>;
}

interface RemotePart {
  PartNumber?: string | number;
  ETag?: string;
  Size?: string | number;
  number?: number;
  etag?: string;
  size?: number;
}

function argument(name: string): string | undefined {
  const prefix = `${name}=`;
  return process.argv
    .find((value) => value.startsWith(prefix))
    ?.slice(prefix.length);
}

function environment(
  publicName: string,
  privateName?: string,
): string | undefined {
  return (
    process.env[publicName]?.trim() ||
    (privateName ? process.env[privateName]?.trim() : undefined)
  );
}

function settings(): OssSettings {
  const region = environment('PUBLIC_ASSET_OSS_REGION', 'OSS_REGION');
  const endpoint = environment('PUBLIC_ASSET_OSS_ENDPOINT', 'OSS_ENDPOINT');
  const bucket = environment('PUBLIC_ASSET_OSS_BUCKET', 'OSS_BUCKET');
  const accessKeyId = environment(
    'PUBLIC_ASSET_OSS_ACCESS_KEY_ID',
    'OSS_ACCESS_KEY_ID',
  );
  const accessKeySecret = environment(
    'PUBLIC_ASSET_OSS_ACCESS_KEY_SECRET',
    'OSS_ACCESS_KEY_SECRET',
  );
  const missing = [
    ['region', region],
    ['endpoint', endpoint],
    ['bucket', bucket],
    ['accessKeyId', accessKeyId],
    ['accessKeySecret', accessKeySecret],
  ]
    .filter(([, value]) => !value)
    .map(([name]) => name);
  if (missing.length) {
    throw new Error(`缺少 OSS 素材配置：${missing.join(', ')}`);
  }

  const regionalEndpoint = normalizeEndpoint(endpoint!, bucket!);
  return {
    region: region!,
    endpoint: regionalEndpoint,
    bucket: bucket!,
    accessKeyId: accessKeyId!,
    accessKeySecret: accessKeySecret!,
    publicBaseUrl:
      environment('PUBLIC_ASSET_BASE_URL') ||
      `https://${bucket!}.${region!}.aliyuncs.com`,
    cdnBaseUrl: environment('PUBLIC_ASSET_CDN_BASE_URL'),
  };
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

async function walk(directory: string): Promise<string[]> {
  const entries = await readdir(directory, { withFileTypes: true });
  const nested = await Promise.all(
    entries
      .filter((entry) => !entry.name.startsWith('.'))
      .map(async (entry) => {
        const path = resolve(directory, entry.name);
        return entry.isDirectory() ? walk(path) : [path];
      }),
  );
  return nested.flat();
}

async function sha256(path: string): Promise<string> {
  const hash = createHash('sha256');
  for await (const chunk of createReadStream(path)) {
    hash.update(chunk as Buffer);
  }
  return hash.digest('hex');
}

async function inventory(
  sourceRoot: string,
  objectPrefix: string,
): Promise<LocalAsset[]> {
  const paths = await walk(sourceRoot);
  const assets: LocalAsset[] = [];
  for (const absolutePath of paths.sort()) {
    const extension = extname(absolutePath).toLowerCase();
    if (!allowedExtensions.has(extension)) continue;
    const file = await stat(absolutePath);
    if (!file.isFile()) continue;
    const relativeKey = relative(sourceRoot, absolutePath).split(sep).join('/');
    assets.push({
      absolutePath,
      objectKey: objectPrefix ? `${objectPrefix}/${relativeKey}` : relativeKey,
      extension,
      sizeBytes: file.size,
      sha256: await sha256(absolutePath),
    });
  }
  return assets;
}

function header(headers: object, name: string): string | number | undefined {
  const value = (headers as Record<string, unknown>)[name];
  return typeof value === 'string' || typeof value === 'number'
    ? value
    : undefined;
}

function remoteHash(headers: object): string | undefined {
  const value =
    header(headers, 'x-oss-meta-sha256') ??
    header(headers, 'X-Oss-Meta-Sha256');
  return typeof value === 'string' ? value : undefined;
}

function isNotFound(error: unknown): boolean {
  if (!error || typeof error !== 'object') return false;
  const candidate = error as { status?: number; code?: string };
  return candidate.status === 404 || candidate.code === 'NoSuchKey';
}

function checkpointPath(objectKey: string): string {
  const id = createHash('sha256').update(objectKey).digest('hex');
  return resolve(checkpointRoot, `${id}.json`);
}

async function removeCheckpoint(path: string): Promise<void> {
  for (const target of [path, `${path}.tmp`]) {
    try {
      await unlink(target);
    } catch (error) {
      const candidate = error as NodeJS.ErrnoException;
      if (candidate.code !== 'ENOENT') throw error;
    }
  }
}

async function saveCheckpoint(
  path: string,
  checkpoint: MultipartCheckpoint,
): Promise<void> {
  await mkdir(checkpointRoot, { recursive: true });
  const temporaryPath = `${path}.tmp`;
  await writeFile(temporaryPath, JSON.stringify(checkpoint), {
    encoding: 'utf8',
    mode: 0o600,
  });
  await rename(temporaryPath, path);
}

async function readCheckpoint(
  path: string,
  asset: LocalAsset,
): Promise<MultipartCheckpoint | undefined> {
  try {
    const checkpoint = JSON.parse(
      await readFile(path, 'utf8'),
    ) as MultipartCheckpoint;
    if (
      checkpoint.file === asset.absolutePath &&
      checkpoint.name === asset.objectKey &&
      checkpoint.fileSize === asset.sizeBytes &&
      checkpoint.partSize === multipartPartSize &&
      checkpoint.uploadId
    ) {
      return checkpoint;
    }
    await removeCheckpoint(path);
  } catch (error) {
    const candidate = error as NodeJS.ErrnoException;
    if (candidate.code !== 'ENOENT') await removeCheckpoint(path);
  }
  return undefined;
}

function normalizedParts(
  parts: RemotePart[],
  fileSize: number,
): Array<{ number: number; etag: string }> | undefined {
  const normalized = parts.map((part) => ({
    number: Number(part.PartNumber ?? part.number),
    etag: String(part.ETag ?? part.etag ?? ''),
    size: Number(part.Size ?? part.size),
  }));
  const valid = normalized.every((part) => {
    const start = (part.number - 1) * multipartPartSize;
    const expectedSize = Math.min(multipartPartSize, fileSize - start);
    return (
      Number.isInteger(part.number) &&
      part.number > 0 &&
      part.etag.length > 0 &&
      part.size === expectedSize
    );
  });
  if (!valid) return undefined;
  return normalized
    .map(({ number, etag }) => ({ number, etag }))
    .sort((left, right) => left.number - right.number);
}

async function remoteCheckpoint(
  client: OSS,
  asset: LocalAsset,
  local?: MultipartCheckpoint,
): Promise<MultipartCheckpoint | undefined> {
  const listed = await client.listUploads({
    prefix: asset.objectKey,
    'max-uploads': 100,
  });
  const uploadIds = new Set(
    listed.uploads
      .filter((upload) => upload.name === asset.objectKey)
      .map((upload) => upload.uploadId),
  );
  if (local) uploadIds.add(local.uploadId);

  let best: MultipartCheckpoint | undefined;
  for (const uploadId of uploadIds) {
    try {
      const result = await client.listParts(asset.objectKey, uploadId, {
        'max-parts': 1000,
        'part-number-marker': 0,
        'encoding-type': 'url',
      });
      const doneParts = normalizedParts(
        result.parts as unknown as RemotePart[],
        asset.sizeBytes,
      );
      if (!doneParts) continue;
      const candidate: MultipartCheckpoint = {
        file: asset.absolutePath,
        name: asset.objectKey,
        fileSize: asset.sizeBytes,
        partSize: multipartPartSize,
        uploadId,
        doneParts,
      };
      if (!best || candidate.doneParts.length > best.doneParts.length) {
        best = candidate;
      }
    } catch (error) {
      if (!isNotFound(error)) throw error;
    }
  }
  return best;
}

async function uploadMultipart(
  client: OSS,
  asset: LocalAsset,
  headers: Record<string, string>,
): Promise<void> {
  const path = checkpointPath(asset.objectKey);
  const local = await readCheckpoint(path, asset);
  const checkpoint = await remoteCheckpoint(client, asset, local);
  if (checkpoint) {
    await saveCheckpoint(path, checkpoint);
    console.log(
      `断点续传：${asset.objectKey}（已完成 ${checkpoint.doneParts.length} 个分片）`,
    );
  }

  let checkpointWrite = Promise.resolve();
  await client.multipartUpload(asset.objectKey, asset.absolutePath, {
    parallel: 3,
    partSize: multipartPartSize,
    checkpoint: checkpoint as OSS.Checkpoint | undefined,
    timeout: ossMultipartTimeout,
    headers,
    progress: (
      _percentage: number,
      nextCheckpoint?: MultipartCheckpoint,
    ): Promise<void> => {
      if (!nextCheckpoint) return Promise.resolve();
      checkpointWrite = checkpointWrite.then(() =>
        saveCheckpoint(path, nextCheckpoint),
      );
      return checkpointWrite;
    },
  });
  await checkpointWrite;
  await removeCheckpoint(path);
}

function publicUrl(baseUrl: string, objectKey: string): string {
  return `${baseUrl.replace(/\/$/, '')}/${objectKey
    .split('/')
    .map(encodeURIComponent)
    .join('/')}`;
}

async function verifyPublicAccess(
  url: string,
  extension: string,
): Promise<void> {
  let lastError: unknown;
  for (let attempt = 1; attempt <= 4; attempt += 1) {
    try {
      const video = ['.mp4', '.webm', '.mov', '.m4v'].includes(extension);
      const response = await fetch(url, {
        method: video ? 'GET' : 'HEAD',
        headers: video ? { Range: 'bytes=0-1023' } : undefined,
        redirect: 'manual',
        signal: AbortSignal.timeout(10_000),
      });
      if (video) {
        const contentRange = response.headers.get('content-range') || '';
        const contentType = response.headers.get('content-type') || '';
        if (
          response.status === 206 &&
          /^bytes 0-1023\/\d+$/.test(contentRange) &&
          contentType.startsWith(mimeTypes[extension])
        ) {
          return;
        }
      } else if (response.ok) {
        return;
      }
      lastError = new Error(`HTTP ${response.status}`);
    } catch (error) {
      lastError = error;
    }
    if (attempt < 4) {
      await new Promise((resolveDelay) =>
        setTimeout(resolveDelay, 500 * 2 ** (attempt - 1)),
      );
    }
  }
  throw lastError;
}

async function main(): Promise<void> {
  const apply = process.argv.includes('--apply');
  const overwrite = process.argv.includes('--overwrite');
  const sourceRoot = resolve(
    argument('--source') || resolve(backendRoot, '../frontend/public'),
  );
  const objectPrefix = (argument('--prefix') || '')
    .replace(/^\/+|\/+$/g, '')
    .trim();
  if (objectPrefix.split('/').some((segment) => segment === '..')) {
    throw new Error('OSS 对象前缀不能包含 ..');
  }
  const oss = settings();
  const client = new OSS({
    region: oss.region,
    endpoint: oss.endpoint,
    bucket: oss.bucket,
    accessKeyId: oss.accessKeyId,
    accessKeySecret: oss.accessKeySecret,
    authorizationV4: true,
    timeout: 60_000,
    retryMax: 2,
  } as OSS.Options & { retryMax: number });
  const assets = await inventory(sourceRoot, objectPrefix);
  const pending: LocalAsset[] = [];
  let skipped = 0;
  const conflicts: LocalAsset[] = [];

  for (const asset of assets) {
    try {
      const result = await client.head(asset.objectKey);
      if (remoteHash(result.res.headers) === asset.sha256) {
        skipped += 1;
      } else if (overwrite) {
        pending.push(asset);
      } else {
        conflicts.push(asset);
      }
    } catch (error) {
      if (isNotFound(error)) pending.push(asset);
      else throw error;
    }
  }

  const totalBytes = assets.reduce((sum, asset) => sum + asset.sizeBytes, 0);
  console.log(
    `素材清单：${assets.length} 个，${(totalBytes / 1024 / 1024).toFixed(2)} MiB`,
  );
  console.log(
    `待上传：${pending.length}，已相同：${skipped}，同名冲突：${conflicts.length}`,
  );

  if (conflicts.length) {
    for (const asset of conflicts) console.log(`冲突：${asset.objectKey}`);
    throw new Error('存在同名不同内容；确认后使用 --overwrite 显式覆盖');
  }
  if (!apply) {
    console.log('当前为预检模式；确认后添加 --apply 执行上传');
    return;
  }

  let uploaded = 0;
  for (const asset of pending) {
    const versionedVideo =
      ['.mp4', '.webm', '.mov', '.m4v'].includes(asset.extension) &&
      /(^|\/)videos\/.+\/v\d+\//.test(asset.objectKey);
    const headers = {
      'Content-Type': mimeTypes[asset.extension],
      'Cache-Control': versionedVideo
        ? 'public, max-age=31536000, immutable'
        : 'public, max-age=3600',
      'x-oss-object-acl': 'public-read',
      'x-oss-meta-sha256': asset.sha256,
    };
    if (asset.sizeBytes >= multipartThreshold) {
      await uploadMultipart(client, asset, headers);
    } else {
      await client.put(asset.objectKey, asset.absolutePath, { headers });
    }

    const verified = await client.head(asset.objectKey);
    const verifiedLength = Number(
      header(verified.res.headers, 'content-length'),
    );
    if (
      remoteHash(verified.res.headers) !== asset.sha256 ||
      verifiedLength !== asset.sizeBytes
    ) {
      throw new Error(`上传后校验失败：${asset.objectKey}`);
    }
    await verifyPublicAccess(
      publicUrl(oss.publicBaseUrl, asset.objectKey),
      asset.extension,
    );
    if (oss.cdnBaseUrl) {
      await verifyPublicAccess(
        publicUrl(oss.cdnBaseUrl, asset.objectKey),
        asset.extension,
      );
    }
    uploaded += 1;
    console.log(`已上传 ${uploaded}/${pending.length}：${asset.objectKey}`);
  }

  for (let start = 0; start < assets.length; start += 8) {
    await Promise.all(
      assets.slice(start, start + 8).map(async (asset) => {
        const urls = [publicUrl(oss.publicBaseUrl, asset.objectKey)];
        if (oss.cdnBaseUrl) {
          urls.push(publicUrl(oss.cdnBaseUrl, asset.objectKey));
        }
        await Promise.all(
          urls.map((url) =>
            verifyPublicAccess(url, asset.extension).catch((error: unknown) => {
              const message =
                error instanceof Error ? error.message : String(error);
              throw new Error(
                `公网读取校验失败：${asset.objectKey} ${url} ${message}`,
              );
            }),
          ),
        );
      }),
    );
  }

  console.log(
    `上传完成：新增/更新 ${uploaded}，跳过 ${skipped}，公网校验 ${assets.length}，公共地址 ${oss.publicBaseUrl}`,
  );
}

main().catch((error: unknown) => {
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
    `素材上传失败：${candidate.message || String(error)} ` +
      `[${candidate.code || 'UNKNOWN'}/${candidate.status || 'UNKNOWN'}/${candidate.requestId || 'NONE'}]`,
  );
  process.exitCode = 1;
});
