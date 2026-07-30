import {
  Injectable,
  Logger,
  OnModuleDestroy,
  OnModuleInit,
  Optional,
} from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { resolve } from 'node:path';
import type { AppEnvironment } from '../platform/config/environment';
import {
  createJobOwner,
  JobLeaseService,
} from '../platform/database/job-lease.service';
import { loadSystemNotificationConfiguration } from './system-notification.config';
import { SystemNotificationRepository } from './system-notification.repository';

@Injectable()
export class SystemNotificationPublisher
  implements OnModuleInit, OnModuleDestroy
{
  private readonly logger = new Logger(SystemNotificationPublisher.name);
  private timer: NodeJS.Timeout | null = null;
  private running = false;
  private readonly ownerId = createJobOwner(SystemNotificationPublisher.name);

  constructor(
    private readonly config: ConfigService<AppEnvironment, true>,
    private readonly repository: SystemNotificationRepository,
    @Optional() private readonly leases?: JobLeaseService,
  ) {}

  async onModuleInit(): Promise<void> {
    if (
      this.config.get('BACKGROUND_JOBS_ENABLED', { infer: true }) === false ||
      !this.config.get('SYSTEM_NOTIFICATION_PUBLISHER_ENABLED', { infer: true })
    )
      return;
    await this.runOnce();
    const interval = this.config.get('SYSTEM_NOTIFICATION_POLL_INTERVAL_MS', {
      infer: true,
    });
    this.timer = setInterval(() => void this.runOnce(), interval);
    this.timer.unref();
  }

  onModuleDestroy(): void {
    if (this.timer) clearInterval(this.timer);
  }

  async runOnce(options: { throwOnError?: boolean } = {}): Promise<void> {
    if (
      this.config.get('BACKGROUND_JOBS_ENABLED', { infer: true }) === false ||
      this.running
    )
      return;
    this.running = true;
    const startedAt = Date.now();
    try {
      const lease = this.leases
        ? await this.leases.runExclusive(
            'system-notification-publisher',
            this.ownerId,
            this.config.get('BACKGROUND_JOB_LEASE_MS', { infer: true }) ??
              180_000,
            () => this.publishOnce(),
          )
        : { acquired: true, result: await this.publishOnce() };
      if (!lease.acquired || !lease.result) return;
      const result = lease.result;
      this.logger.log({
        event: 'system_notification_publish_finished',
        configuredPublications: result.configuredPublications,
        publishedBatches: result.publishedBatches,
        durationMs: Date.now() - startedAt,
      });
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Unknown error';
      this.logger.error({
        event: 'system_notification_publish_failed',
        durationMs: Date.now() - startedAt,
        error: message,
      });
      if (options.throwOnError) throw error;
    } finally {
      this.running = false;
    }
  }

  private async publishOnce(): Promise<{
    configuredPublications: number;
    publishedBatches: number;
  }> {
    const configuredPath = this.config.get('SYSTEM_NOTIFICATION_CONFIG_PATH', {
      infer: true,
    });
    const configuration = await loadSystemNotificationConfiguration(
      resolve(process.cwd(), configuredPath),
    );
    for (const publication of configuration.publications) {
      await this.repository.syncPublication(publication);
    }
    const published = await this.repository.publishDue(
      this.config.get('SYSTEM_NOTIFICATION_BATCH_SIZE', { infer: true }),
    );
    return {
      configuredPublications: configuration.publications.length,
      publishedBatches: published,
    };
  }
}
