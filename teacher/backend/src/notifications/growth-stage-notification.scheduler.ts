import {
  Injectable,
  Logger,
  OnModuleDestroy,
  OnModuleInit,
  Optional,
} from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import type { AppEnvironment } from '../platform/config/environment';
import {
  createJobOwner,
  JobLeaseService,
} from '../platform/database/job-lease.service';
import { GrowthStageNotificationRepository } from './growth-stage-notification.repository';

@Injectable()
export class GrowthStageNotificationScheduler
  implements OnModuleInit, OnModuleDestroy
{
  private readonly logger = new Logger(GrowthStageNotificationScheduler.name);
  private timer: NodeJS.Timeout | null = null;
  private running = false;
  private readonly ownerId = createJobOwner(
    GrowthStageNotificationScheduler.name,
  );

  constructor(
    private readonly config: ConfigService<AppEnvironment, true>,
    private readonly repository: GrowthStageNotificationRepository,
    @Optional() private readonly leases?: JobLeaseService,
  ) {}

  async onModuleInit(): Promise<void> {
    if (
      this.config.get('BACKGROUND_JOBS_ENABLED', { infer: true }) === false ||
      !this.config.get('GROWTH_STAGE_NOTIFICATION_SCHEDULER_ENABLED', {
        infer: true,
      })
    ) {
      return;
    }

    await this.runOnce();
    const interval = this.config.get(
      'GROWTH_STAGE_NOTIFICATION_POLL_INTERVAL_MS',
      { infer: true },
    );
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
      const work = () => this.repository.scanAndCreate(new Date(), 200);
      const lease = this.leases
        ? await this.leases.runExclusive(
            'growth-stage-notifications',
            this.ownerId,
            this.config.get('BACKGROUND_JOB_LEASE_MS', { infer: true }) ??
              180_000,
            work,
          )
        : { acquired: true, result: await work() };
      if (!lease.acquired || !lease.result) return;
      const result = lease.result;
      this.logger.log({
        event: 'growth_stage_notification_scan_finished',
        scannedTeachers: result.scannedTeachers,
        initializedTeachers: result.initializedTeachers,
        createdNotifications: result.createdNotifications,
        durationMs: Date.now() - startedAt,
      });
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Unknown error';
      this.logger.error({
        event: 'growth_stage_notification_scan_failed',
        durationMs: Date.now() - startedAt,
        error: message,
      });
      if (options.throwOnError) throw error;
    } finally {
      this.running = false;
    }
  }
}
