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
import { SupportTicketService } from './support-ticket.service';

@Injectable()
export class SupportTicketScheduler implements OnModuleInit, OnModuleDestroy {
  private readonly logger = new Logger(SupportTicketScheduler.name);
  private timer: NodeJS.Timeout | null = null;
  private running = false;
  private readonly ownerId = createJobOwner(SupportTicketScheduler.name);

  constructor(
    private readonly config: ConfigService<AppEnvironment, true>,
    private readonly tickets: SupportTicketService,
    private readonly leases: JobLeaseService,
  ) {}

  async onModuleInit(): Promise<void> {
    if (this.config.get('BACKGROUND_JOBS_ENABLED', { infer: true }) === false) {
      return;
    }
    await this.runOnce();
    const interval = this.config.get(
      'SUPPORT_TICKET_CLEANUP_POLL_INTERVAL_MS',
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
    try {
      const work = (activeLease: { assertActive(): void }) =>
        this.tickets.closeExpiredAndCleanup(
          this.config.get('SUPPORT_TICKET_CLEANUP_BATCH_SIZE', { infer: true }),
          () => activeLease.assertActive(),
        );
      await this.leases.runExclusive(
        'support-ticket-cleanup',
        this.ownerId,
        this.config.get('BACKGROUND_JOB_LEASE_MS', { infer: true }) ?? 180_000,
        work,
      );
    } catch (error) {
      this.logger.error({
        event: 'support_ticket_cleanup_failed',
        error: error instanceof Error ? error.message : 'Unknown error',
      });
      if (options.throwOnError) throw error;
    } finally {
      this.running = false;
    }
  }
}
