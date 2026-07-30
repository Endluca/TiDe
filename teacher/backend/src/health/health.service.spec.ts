import type { ConfigService } from '@nestjs/config';
import { HealthService } from './health.service';
import type { DatabaseService } from '../platform/database/database.service';
import type { AppEnvironment } from '../platform/config/environment';
import { DependencyHealthRegistry } from '../platform/observability/dependency-health.registry';

describe('HealthService', () => {
  const config = {
    get: jest.fn((key: keyof AppEnvironment) => {
      if (key === 'FILE_STORAGE_PROVIDER') return 'OSS';
      if (key === 'MAIL_DELIVERY_PROVIDER') return 'COMPANY_MESSAGE_API';
      if (key === 'AI_GATEWAY_ENABLED') return true;
      if (key === 'BACKGROUND_JOBS_ENABLED') return true;
      return undefined;
    }),
  } as unknown as ConfigService<AppEnvironment, true>;

  it('returns the service health status', () => {
    const database = {} as DatabaseService;
    const service = new HealthService(
      database,
      config,
      new DependencyHealthRegistry(),
    );

    expect(service.getStatus()).toEqual({
      status: 'ok',
      service: 'tide-backend',
    });
  });

  it('reports readiness from database checks', async () => {
    const database = {
      checkReadiness: jest.fn().mockResolvedValue({
        tide: 'ok',
        shiwenRead: 'ok',
      }),
    } as unknown as DatabaseService;
    const service = new HealthService(
      database,
      config,
      new DependencyHealthRegistry(),
    );

    await expect(service.getReadiness()).resolves.toEqual({
      status: 'ready',
      service: 'tide-backend',
      checks: { tide: 'ok', shiwenRead: 'ok' },
    });
  });

  it.each(['not_configured', 'unavailable'] as const)(
    'is not ready when the Shiwen read database is %s',
    async (shiwenRead) => {
      const database = {
        checkReadiness: jest.fn().mockResolvedValue({
          tide: 'ok',
          shiwenRead,
        }),
      } as unknown as DatabaseService;
      const service = new HealthService(
        database,
        config,
        new DependencyHealthRegistry(),
      );

      await expect(service.getReadiness()).resolves.toEqual({
        status: 'not_ready',
        service: 'tide-backend',
        checks: { tide: 'ok', shiwenRead },
      });
    },
  );

  it('reports configured and recently checked dependencies without probing them', async () => {
    const checkReadiness = jest.fn().mockResolvedValue({
      tide: 'ok',
      shiwenRead: 'ok',
    });
    const database = {
      checkReadiness,
      getPoolStats: jest.fn().mockReturnValue({
        tide: {
          configured: true,
          totalConnections: 4,
          idleConnections: 2,
          waitingRequests: 0,
        },
        shiwenRead: {
          configured: true,
          totalConnections: 2,
          idleConnections: 1,
          waitingRequests: 0,
        },
      }),
    } as unknown as DatabaseService;
    const dependencyHealth = new DependencyHealthRegistry();
    dependencyHealth.recordSuccess('fileStorage', Date.now());
    dependencyHealth.recordFailure(
      'aiGateway',
      'AI_GATEWAY_UNAVAILABLE',
      Date.now(),
    );
    const service = new HealthService(database, config, dependencyHealth);

    const result = await service.getDependencies();

    expect(result).toMatchObject({
      status: 'degraded',
      service: 'tide-backend',
      checks: {
        database: { tide: 'ok', shiwenRead: 'ok' },
        databasePools: {
          tide: { totalConnections: 4, waitingRequests: 0 },
          shiwenRead: { totalConnections: 2, waitingRequests: 0 },
        },
        fileStorage: { provider: 'OSS', status: 'ok' },
        aiGateway: {
          enabled: true,
          status: 'error',
          errorCode: 'AI_GATEWAY_UNAVAILABLE',
        },
        mail: { status: 'configured' },
        backgroundJobs: { status: 'enabled' },
      },
    });
    expect(typeof result.checks.runtime.rssBytes).toBe('number');
    expect(typeof result.checks.runtime.heapUsedBytes).toBe('number');
    expect(typeof result.checks.runtime.uptimeSeconds).toBe('number');
    expect(result.checkedAt).toEqual(expect.any(String));
    expect(checkReadiness).toHaveBeenCalledTimes(1);
  });
});
