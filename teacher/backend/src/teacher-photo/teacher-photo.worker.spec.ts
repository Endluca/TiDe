import type { ConfigService } from '@nestjs/config';
import type { AppEnvironment } from '../platform/config/environment';
import { JobLeaseLostError } from '../platform/database/job-lease.service';
import type { TeacherPhotoRunRecord } from './teacher-photo.models';
import type { TeacherPhotoRepository } from './teacher-photo.repository';
import type { TeacherPhotoService } from './teacher-photo.service';
import { TeacherPhotoWorker } from './teacher-photo.worker';

function config(
  values: Partial<AppEnvironment>,
): ConfigService<AppEnvironment, true> {
  return {
    get: jest.fn((key: keyof AppEnvironment) => values[key]),
  } as unknown as ConfigService<AppEnvironment, true>;
}

const pendingRun = {
  photoRunId: 'photo-run',
  status: 'CHECKING',
} as TeacherPhotoRunRecord;

describe('TeacherPhotoWorker', () => {
  it('does not scan when background jobs are disabled', async () => {
    const repository = { claimPending: jest.fn() };
    const photos = { processPending: jest.fn() };
    const worker = new TeacherPhotoWorker(
      config({
        BACKGROUND_JOBS_ENABLED: false,
        TEACHER_PHOTO_WORKER_POLL_INTERVAL_MS: 1_000,
        TEACHER_PHOTO_WORKER_BATCH_SIZE: 2,
        TEACHER_PHOTO_WORKER_LEASE_MS: 120_000,
      }),
      repository as unknown as TeacherPhotoRepository,
      photos as unknown as TeacherPhotoService,
    );

    await worker.runOnce({ throwOnError: true });

    expect(repository.claimPending).not.toHaveBeenCalled();
  });

  it('processes a bounded batch without overlapping scans', async () => {
    let finishProcessing: (() => void) | undefined;
    const repository = {
      claimPending: jest.fn().mockResolvedValue([pendingRun]),
    };
    const photos = {
      processPending: jest.fn(
        () =>
          new Promise<void>((resolve) => {
            finishProcessing = resolve;
          }),
      ),
    };
    const worker = new TeacherPhotoWorker(
      config({
        BACKGROUND_JOBS_ENABLED: true,
        TEACHER_PHOTO_WORKER_POLL_INTERVAL_MS: 1_000,
        TEACHER_PHOTO_WORKER_BATCH_SIZE: 2,
        TEACHER_PHOTO_WORKER_LEASE_MS: 120_000,
      }),
      repository as unknown as TeacherPhotoRepository,
      photos as unknown as TeacherPhotoService,
    );

    const first = worker.runOnce({ throwOnError: true });
    await Promise.resolve();
    const overlapping = worker.runOnce({ throwOnError: true });

    expect(repository.claimPending).toHaveBeenCalledWith(
      expect.stringContaining(`:${process.pid}:`),
      2,
      120_000,
    );
    expect(repository.claimPending).toHaveBeenCalledTimes(1);
    await expect(overlapping).resolves.toBeUndefined();
    finishProcessing?.();
    await expect(first).resolves.toBeUndefined();
    expect(photos.processPending).toHaveBeenCalledWith(
      pendingRun,
      expect.any(Function),
    );
  });

  it('renews row leases and stops the batch when ownership is lost', async () => {
    jest.useFakeTimers();
    try {
      let continueProcessing: (() => void) | undefined;
      let processingStarted: (() => void) | undefined;
      const started = new Promise<void>((resolve) => {
        processingStarted = resolve;
      });
      const repository = {
        claimPending: jest.fn().mockResolvedValue([pendingRun]),
        renewClaims: jest.fn().mockResolvedValue([]),
      };
      const photos = {
        processPending: jest.fn(
          async (
            _run: TeacherPhotoRunRecord,
            assertLeaseActive: () => void,
          ) => {
            processingStarted?.();
            await new Promise<void>((resolve) => {
              continueProcessing = resolve;
            });
            assertLeaseActive();
          },
        ),
      };
      const worker = new TeacherPhotoWorker(
        config({
          BACKGROUND_JOBS_ENABLED: true,
          TEACHER_PHOTO_WORKER_POLL_INTERVAL_MS: 1_000,
          TEACHER_PHOTO_WORKER_BATCH_SIZE: 2,
          TEACHER_PHOTO_WORKER_LEASE_MS: 30_000,
        }),
        repository as unknown as TeacherPhotoRepository,
        photos as unknown as TeacherPhotoService,
      );

      const running = worker.runOnce({ throwOnError: true });
      await started;
      await jest.advanceTimersByTimeAsync(10_000);
      continueProcessing?.();

      await expect(running).rejects.toBeInstanceOf(JobLeaseLostError);
      expect(repository.renewClaims).toHaveBeenCalledWith(
        expect.stringContaining(`:${process.pid}:`),
        ['photo-run'],
        30_000,
      );
    } finally {
      jest.useRealTimers();
    }
  });
});
