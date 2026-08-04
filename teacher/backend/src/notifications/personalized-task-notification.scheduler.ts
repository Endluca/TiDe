import {
  Injectable,
  Logger,
  OnModuleDestroy,
  OnModuleInit,
} from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import type { AppEnvironment } from '../platform/config/environment';
import {
  createJobOwner,
  JobLeaseService,
} from '../platform/database/job-lease.service';
import { PersonalizedTaskNotificationRepository } from './personalized-task-notification.repository';

@Injectable()
export class PersonalizedTaskNotificationScheduler
  implements OnModuleInit, OnModuleDestroy
{
  private readonly logger = new Logger(
    PersonalizedTaskNotificationScheduler.name,
  );
  private timer: NodeJS.Timeout | null = null;
  private running = false;
  private readonly ownerId = createJobOwner(
    PersonalizedTaskNotificationScheduler.name,
  );

  constructor(
    private readonly config: ConfigService<AppEnvironment, true>,
    private readonly repository: PersonalizedTaskNotificationRepository,
    private readonly leases: JobLeaseService,
  ) {}

  async onModuleInit(): Promise<void> {
    if (
      this.config.get('BACKGROUND_JOBS_ENABLED', { infer: true }) === false ||
      !this.config.get('PERSONALIZED_TASK_NOTIFICATION_SCHEDULER_ENABLED', {
        infer: true,
      })
    ) {
      return;
    }

    await this.runOnce();
    const interval = this.config.get(
      'PERSONALIZED_TASK_NOTIFICATION_POLL_INTERVAL_MS',
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
      const configuredRolloutAt = this.config.get(
        'PERSONALIZED_TASK_NOTIFICATION_ROLLOUT_AT',
        { infer: true },
      );
      if (!configuredRolloutAt) {
        throw new Error('Personalized task notification rollout is missing');
      }
      const work = async (lease: { assertActive(): void }) => {
        lease.assertActive();
        const result = await this.repository.scanAndCreate(
          new Date(),
          new Date(configuredRolloutAt),
          200,
        );
        lease.assertActive();
        return result;
      };
      const lease = await this.leases.runExclusive(
        'personalized-task-notifications',
        this.ownerId,
        this.config.get('BACKGROUND_JOB_LEASE_MS', { infer: true }) ?? 180_000,
        work,
      );
      if (!lease.acquired || !lease.result) return;
      const result = lease.result;
      this.logger.log({
        event: 'personalized_task_notification_scan_finished',
        scannedAssignments: result.scannedAssignments,
        createdNotifications: result.createdNotifications,
        durationMs: Date.now() - startedAt,
      });
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Unknown error';
      this.logger.error({
        event: 'personalized_task_notification_scan_failed',
        durationMs: Date.now() - startedAt,
        error: message,
      });
      if (options.throwOnError) throw error;
    } finally {
      this.running = false;
    }
  }
}
