import type { ConfigService } from '@nestjs/config';
import { Pool, type PoolConfig } from 'pg';
import type { AppEnvironment } from '../config/environment';

function createPoolConfig(
  connectionString: string,
  applicationName: string,
  config: ConfigService<AppEnvironment, true>,
): PoolConfig {
  return {
    connectionString,
    application_name: applicationName,
    max: config.get('DATABASE_MAX_CONNECTIONS', { infer: true }),
    connectionTimeoutMillis: config.get('DATABASE_CONNECTION_TIMEOUT_MS', {
      infer: true,
    }),
    statement_timeout: config.get('DATABASE_STATEMENT_TIMEOUT_MS', {
      infer: true,
    }),
    idleTimeoutMillis: 30_000,
  };
}

export function createTidePool(
  config: ConfigService<AppEnvironment, true>,
): Pool | null {
  const connectionString = config.get('TIDE_DATABASE_URL', { infer: true });

  if (!connectionString) {
    return null;
  }

  return new Pool(createPoolConfig(connectionString, 'tide-backend', config));
}

export function createShiwenReadPool(
  config: ConfigService<AppEnvironment, true>,
): Pool | null {
  const connectionString = config.get('SHIWEN_READ_DATABASE_URL', {
    infer: true,
  });

  if (!connectionString) {
    return null;
  }

  return new Pool(
    createPoolConfig(connectionString, 'tide-backend-shiwen-read', config),
  );
}
