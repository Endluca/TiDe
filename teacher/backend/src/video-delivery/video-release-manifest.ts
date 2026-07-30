import { access, readFile } from 'node:fs/promises';
import { resolve } from 'node:path';

export interface VideoDatabaseBinding {
  taskCode: string;
  stepKey: string;
  configRoot?: string;
  mediaVersion: string;
}

export interface PublicVideoRelease {
  taskCodes: string[];
  titleZh: string;
  sourcePath: string;
  targetPath: string;
  durationSeconds: number;
  processing: 'remux' | 'transcode';
  bindings: VideoDatabaseBinding[];
}

export interface PublicVideoReleaseManifest {
  version: string;
  sourceBaseUrl: string;
  cdnBaseUrl: string;
  videos: PublicVideoRelease[];
}

function assertPath(path: string, name: string): void {
  if (!path.startsWith('/') || path.includes('..')) {
    throw new Error(`${name} 必须是安全的绝对 URL 路径：${path}`);
  }
}

export function publicVideoUrl(baseUrl: string, path: string): string {
  assertPath(path, '视频路径');
  return `${baseUrl.replace(/\/$/, '')}${path
    .split('/')
    .map((part) => encodeURIComponent(part))
    .join('/')}`;
}

export function releaseVideoUrls(
  manifest: PublicVideoReleaseManifest,
): string[] {
  return manifest.videos.map((video) =>
    publicVideoUrl(manifest.cdnBaseUrl, video.targetPath),
  );
}

export async function readVideoReleaseManifest(
  path: string,
): Promise<PublicVideoReleaseManifest> {
  const manifest = JSON.parse(
    await readFile(resolve(path), 'utf8'),
  ) as PublicVideoReleaseManifest;
  if (!manifest.version?.trim()) throw new Error('视频清单缺少 version');
  if (!manifest.cdnBaseUrl?.trim()) throw new Error('视频清单缺少 cdnBaseUrl');
  if (!Array.isArray(manifest.videos) || manifest.videos.length === 0) {
    throw new Error('视频清单没有视频');
  }
  if (manifest.videos.length > 100) {
    throw new Error(`单次预热最多 100 个 URL，当前 ${manifest.videos.length}`);
  }
  const paths = new Set<string>();
  for (const video of manifest.videos) {
    assertPath(video.sourcePath, 'sourcePath');
    assertPath(video.targetPath, 'targetPath');
    if (paths.has(video.targetPath)) {
      throw new Error(`视频清单存在重复 targetPath：${video.targetPath}`);
    }
    paths.add(video.targetPath);
    if (!Array.isArray(video.bindings) || video.bindings.length === 0) {
      throw new Error(`视频缺少数据库绑定：${video.targetPath}`);
    }
    for (const binding of video.bindings) {
      if (!binding.taskCode || !binding.stepKey || !binding.mediaVersion) {
        throw new Error(`数据库绑定不完整：${video.targetPath}`);
      }
      if (
        binding.configRoot &&
        !binding.configRoot
          .split('.')
          .every((part) => /^[a-zA-Z0-9_]+$/.test(part))
      ) {
        throw new Error(`configRoot 不合法：${binding.configRoot}`);
      }
    }
  }
  return manifest;
}

export async function assertReleaseFilesExist(
  manifest: PublicVideoReleaseManifest,
  sourceRoot: string,
): Promise<void> {
  await Promise.all(
    manifest.videos.map((video) =>
      access(resolve(sourceRoot, `.${video.targetPath}`)),
    ),
  );
}

export async function verifyVideoRangeUrls(
  urls: string[],
  options?: { attempts?: number; timeoutMilliseconds?: number },
): Promise<void> {
  const attempts = options?.attempts || 4;
  const timeoutMilliseconds = options?.timeoutMilliseconds || 15_000;
  const failures: string[] = [];
  for (let start = 0; start < urls.length; start += 6) {
    await Promise.all(
      urls.slice(start, start + 6).map(async (url) => {
        let lastMessage = '';
        for (let attempt = 1; attempt <= attempts; attempt += 1) {
          try {
            const response = await fetch(url, {
              headers: { Range: 'bytes=0-1023' },
              redirect: 'manual',
              signal: AbortSignal.timeout(timeoutMilliseconds),
            });
            const contentRange = response.headers.get('content-range') || '';
            if (
              response.status === 206 &&
              /^bytes 0-1023\/\d+$/.test(contentRange)
            ) {
              return;
            }
            lastMessage = `HTTP ${response.status}, Content-Range=${contentRange || 'NONE'}`;
          } catch (error) {
            lastMessage =
              error instanceof Error ? error.message : String(error);
          }
          if (attempt < attempts) {
            await new Promise((resolveDelay) =>
              setTimeout(resolveDelay, 500 * 2 ** (attempt - 1)),
            );
          }
        }
        failures.push(`${url} (${lastMessage})`);
      }),
    );
  }
  if (failures.length > 0) {
    throw new Error(
      `以下视频不支持 206 Range，停止预热和版本切换：\n${failures.join('\n')}`,
    );
  }
}
