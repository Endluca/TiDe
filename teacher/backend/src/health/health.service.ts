import { Injectable } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import {
  DatabaseService,
  type DatabasePoolStats,
  type DatabaseReadiness,
} from '../platform/database/database.service';
import type { AppEnvironment } from '../platform/config/environment';
import {
  DependencyHealthRegistry,
  type ExternalDependencyHealth,
} from '../platform/observability/dependency-health.registry';

export interface HealthStatus {
  status: 'ok';
  service: 'tide-backend';
}

export interface ReadinessStatus {
  status: 'ready' | 'not_ready';
  service: 'tide-backend';
  checks: DatabaseReadiness;
}

export interface DependencyStatus {
  status: 'healthy' | 'degraded';
  service: 'tide-backend';
  checkedAt: string;
  checks: {
    database: DatabaseReadiness;
    databasePools: DatabasePoolStats;
    runtime: {
      rssBytes: number;
      heapUsedBytes: number;
      heapTotalBytes: number;
      uptimeSeconds: number;
    };
    fileStorage: ExternalDependencyHealth & {
      provider: AppEnvironment['FILE_STORAGE_PROVIDER'];
    };
    aiGateway: ExternalDependencyHealth & {
      enabled: boolean;
    };
    mail: ExternalDependencyHealth;
    backgroundJobs: {
      status: 'enabled' | 'disabled';
    };
  };
}

@Injectable()
export class HealthService {
  constructor(
    private readonly database: DatabaseService,
    private readonly config: ConfigService<AppEnvironment, true>,
    private readonly dependencyHealth: DependencyHealthRegistry,
  ) {}

  getStatus(): HealthStatus {
    return {
      status: 'ok',
      service: 'tide-backend',
    };
  }

  async getReadiness(): Promise<ReadinessStatus> {
    const checks = await this.database.checkReadiness();
    const status =
      checks.tide === 'ok' && checks.shiwenRead === 'ok'
        ? 'ready'
        : 'not_ready';

    return {
      status,
      service: 'tide-backend',
      checks,
    };
  }

  async getDependencies(): Promise<DependencyStatus> {
    const database = await this.database.checkReadiness();
    const aiEnabled = this.config.get('MODELARK_ENABLED', { infer: true });
    const mailConfigured =
      this.config.get('MAIL_DELIVERY_PROVIDER', { infer: true }) ===
      'COMPANY_MESSAGE_API';
    const checks: DependencyStatus['checks'] = {
      database,
      databasePools: this.database.getPoolStats(),
      runtime: {
        rssBytes: process.memoryUsage().rss,
        heapUsedBytes: process.memoryUsage().heapUsed,
        heapTotalBytes: process.memoryUsage().heapTotal,
        uptimeSeconds: Math.round(process.uptime()),
      },
      fileStorage: {
        provider: this.config.get('FILE_STORAGE_PROVIDER', { infer: true }),
        ...this.dependencyHealth.snapshot('fileStorage', 'configured'),
      },
      aiGateway: {
        enabled: aiEnabled,
        ...this.dependencyHealth.snapshot(
          'aiGateway',
          aiEnabled ? 'configured' : 'disabled',
        ),
      },
      mail: this.dependencyHealth.snapshot(
        'mail',
        mailConfigured ? 'configured' : 'not_configured',
      ),
      backgroundJobs: {
        status: this.config.get('BACKGROUND_JOBS_ENABLED', { infer: true })
          ? 'enabled'
          : 'disabled',
      },
    };
    const degraded =
      database.tide !== 'ok' ||
      database.shiwenRead === 'unavailable' ||
      checks.fileStorage.status === 'error' ||
      checks.aiGateway.status === 'error' ||
      checks.mail.status === 'error' ||
      checks.mail.status === 'not_configured';

    return {
      status: degraded ? 'degraded' : 'healthy',
      service: 'tide-backend',
      checkedAt: new Date().toISOString(),
      checks,
    };
  }
}
