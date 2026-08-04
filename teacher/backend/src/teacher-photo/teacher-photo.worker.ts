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
import { JobLeaseLostError } from '../platform/database/job-lease.service';
import type { TeacherPhotoRunRecord } from './teacher-photo.models';
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

      await this.processBatch(pending);
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

  private async processBatch(pending: TeacherPhotoRunRecord[]): Promise<void> {
    const activePhotoRunIds = new Set(pending.map((run) => run.photoRunId));
    let renewal = Promise.resolve();
    let leaseError: JobLeaseLostError | null = null;
    const assertClaimsActive = (): void => {
      if (leaseError) throw leaseError;
    };
    const renewTimer = setInterval(
      () => {
        renewal = renewal.then(async () => {
          if (leaseError || activePhotoRunIds.size === 0) return;
          const renewingIds = [...activePhotoRunIds];
          try {
            const renewedIds = new Set(
              await this.repository.renewClaims(
                this.processingOwner,
                renewingIds,
                this.leaseMs,
              ),
            );
            const lostIds = renewingIds.filter(
              (photoRunId) =>
                activePhotoRunIds.has(photoRunId) &&
                !renewedIds.has(photoRunId),
            );
            if (lostIds.length > 0) {
              leaseError = new JobLeaseLostError(
                'teacher-photo-row-claims',
                this.processingOwner,
                { lostPhotoRunIds: lostIds },
              );
            }
          } catch (error) {
            leaseError = new JobLeaseLostError(
              'teacher-photo-row-claims',
              this.processingOwner,
              error,
            );
          }
        });
      },
      Math.max(10_000, Math.floor(this.leaseMs / 3)),
    );
    renewTimer.unref();
    try {
      await Promise.all(
        pending.map(async (run) => {
          try {
            assertClaimsActive();
            await this.photos.processPending(run, assertClaimsActive);
          } finally {
            activePhotoRunIds.delete(run.photoRunId);
          }
        }),
      );
      await renewal;
      assertClaimsActive();
    } finally {
      clearInterval(renewTimer);
      await renewal;
    }
  }
}
