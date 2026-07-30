import { Client, ClientConfig } from 'pg';
import {
  PublicVideoReleaseManifest,
  VideoDatabaseBinding,
} from './video-release-manifest';

interface StepRow {
  id: string;
  task_code: string;
  step_key: string;
  config: Record<string, unknown>;
}

function databaseConfig(): ClientConfig {
  const connectionString =
    process.env.TASK_CATALOG_DATABASE_URL?.trim() ||
    process.env.TIDE_DATABASE_URL?.trim();
  if (connectionString) return { connectionString };
  const user = process.env.TIDE_DB_USER?.trim();
  const password = process.env.TIDE_DB_PASSWORD;
  const database = process.env.TIDE_DB_NAME?.trim();
  const missing = [
    ['TIDE_DB_USER', user],
    ['TIDE_DB_PASSWORD', password],
    ['TIDE_DB_NAME', database],
  ]
    .filter(([, value]) => !value)
    .map(([name]) => name);
  if (missing.length > 0) {
    throw new Error(
      `缺少数据库环境变量：${missing.join(', ')}；也可设置 TIDE_DATABASE_URL`,
    );
  }
  return {
    host: process.env.TIDE_DB_HOST?.trim() || '127.0.0.1',
    port: Number(process.env.TIDE_DB_PORT || 55432),
    user,
    password,
    database,
  };
}

function cloneConfig(config: Record<string, unknown>): Record<string, unknown> {
  return JSON.parse(JSON.stringify(config)) as Record<string, unknown>;
}

function rootObject(
  config: Record<string, unknown>,
  configRoot?: string,
): Record<string, unknown> {
  let current = config;
  for (const part of configRoot?.split('.').filter(Boolean) || []) {
    const next = current[part];
    if (!next || typeof next !== 'object' || Array.isArray(next)) {
      throw new Error(`视频配置路径不存在：${configRoot}`);
    }
    current = next as Record<string, unknown>;
  }
  return current;
}

async function findStep(
  client: Client,
  binding: VideoDatabaseBinding,
  lock: boolean,
): Promise<StepRow> {
  const result = await client.query<StepRow>(
    `
      SELECT step.id, execution.task_code, step.step_key, step.config
      FROM tide.task_step_definitions step
      JOIN tide.task_execution_versions execution
        ON execution.id = step.execution_version_id
      WHERE execution.task_code = $1
        AND execution.status = 'ACTIVE'
        AND step.step_key = $2
      ${lock ? 'FOR UPDATE OF step' : ''}
    `,
    [binding.taskCode, binding.stepKey],
  );
  if (result.rows.length !== 1) {
    throw new Error(
      `找不到唯一的生效视频步骤：${binding.taskCode}/${binding.stepKey}`,
    );
  }
  return result.rows[0];
}

async function withClient<T>(
  operation: (client: Client) => Promise<T>,
): Promise<T> {
  const client = new Client(databaseConfig());
  await client.connect();
  try {
    return await operation(client);
  } finally {
    await client.end();
  }
}

export async function currentReleaseVideoPaths(
  manifest: PublicVideoReleaseManifest,
): Promise<string[]> {
  return withClient(async (client) => {
    const paths = new Set<string>();
    for (const video of manifest.videos) {
      for (const binding of video.bindings) {
        const row = await findStep(client, binding, false);
        const current = rootObject(row.config, binding.configRoot);
        const assetUrl = current.assetUrl;
        const mediaVersion = current.mediaVersion;
        if (typeof assetUrl !== 'string' || typeof mediaVersion !== 'string') {
          throw new Error(
            `当前视频配置不完整：${binding.taskCode}/${binding.stepKey}`,
          );
        }
        if (!assetUrl.includes('/v2/')) {
          throw new Error(
            `当前使用的不是 v2，停止回热：${binding.taskCode}/${binding.stepKey} ${assetUrl}`,
          );
        }
        paths.add(assetUrl);
      }
    }
    return [...paths].sort();
  });
}

export async function switchReleaseVideoBindings(
  manifest: PublicVideoReleaseManifest,
): Promise<{ changed: number; unchanged: number }> {
  return withClient(async (client) => {
    await client.query('BEGIN');
    let changed = 0;
    let unchanged = 0;
    try {
      for (const video of manifest.videos) {
        for (const binding of video.bindings) {
          const row = await findStep(client, binding, true);
          const config = cloneConfig(row.config);
          const current = rootObject(config, binding.configRoot);
          if (
            current.assetUrl === video.targetPath &&
            current.mediaVersion === binding.mediaVersion
          ) {
            unchanged += 1;
            continue;
          }
          current.assetUrl = video.targetPath;
          current.mediaVersion = binding.mediaVersion;
          await client.query(
            `
              UPDATE tide.task_step_definitions
              SET config = $2
              WHERE id = $1
            `,
            [row.id, config],
          );
          changed += 1;
        }
      }
      await client.query('COMMIT');
      return { changed, unchanged };
    } catch (error) {
      await client.query('ROLLBACK');
      throw error;
    }
  });
}
