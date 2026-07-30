import {
  Injectable,
  Logger,
  OnModuleDestroy,
  OnModuleInit,
} from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { randomUUID } from 'node:crypto';
import { hostname } from 'node:os';
import type { AppEnvironment } from '../platform/config/environment';
import { TeacherPhotoRepository } from './teacher-photo.repository';
import { TeacherPhotoService } from './teacher-photo.service';

@Injectable()
export class TeacherPhotoWorker implements OnModuleInit, OnModuleDestroy {
  private readonly logger = new Logger(TeacherPhotoWorker.name);
  private readonly enabled: boolean;
  private readonly pollIntervalMs: number;
  private readonly batchSize: number;
  private readonly leaseMs: number;
  private readonly processingOwner = `${hostname()}:${process.pid}:${randomUUID()}`;
  private timer: NodeJS.Timeout | null = null;
  private running = false;

  constructor(
    config: ConfigService<AppEnvironment, true>,
    private readonly repository: TeacherPhotoRepository,
    private readonly photos: TeacherPhotoService,
  ) {
    this.enabled = config.get('BACKGROUND_JOBS_ENABLED', { infer: true });
    this.pollIntervalMs = config.get('TEACHER_PHOTO_WORKER_POLL_INTERVAL_MS', {
      infer: true,
    });
    this.batchSize = config.get('TEACHER_PHOTO_WORKER_BATCH_SIZE', {
      infer: true,
    });
    this.leaseMs = config.get('TEACHER_PHOTO_WORKER_LEASE_MS', {
      infer: true,
    });
  }

  async onModuleInit(): Promise<void> {
    if (!this.enabled) return;

    await this.runOnce();
    this.timer = setInterval(() => void this.runOnce(), this.pollIntervalMs);
    this.timer.unref();
  }

  onModuleDestroy(): void {
    if (this.timer) clearInterval(this.timer);
  }

  async runOnce(options: { throwOnError?: boolean } = {}): Promise<void> {
    if (!this.enabled || this.running) return;
    this.running = true;
    const startedAt = Date.now();
    try {
      const pending = await this.repository.claimPending(
        this.processingOwner,
        this.batchSize,
        this.leaseMs,
      );
      if (pending.length === 0) return;

      await Promise.all(pending.map((run) => this.photos.processPending(run)));
      this.logger.log({
        event: 'teacher_photo_worker_batch_finished',
        processedCount: pending.length,
        durationMs: Date.now() - startedAt,
        maxQueueDelayMs: Math.max(
          ...pending.map((run) =>
            run.submittedAt instanceof Date
              ? Math.max(0, startedAt - run.submittedAt.getTime())
              : 0,
          ),
        ),
        maxAttemptCount: Math.max(
          ...pending.map((run) => run.attemptCount ?? 1),
        ),
      });
    } catch (error) {
      this.logger.error({
        event: 'teacher_photo_worker_batch_failed',
        durationMs: Date.now() - startedAt,
        errorCode: error instanceof Error ? error.name : 'UNKNOWN_ERROR',
      });
      if (options.throwOnError) throw error;
    } finally {
      this.running = false;
    }
  }
}
