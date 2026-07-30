import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { resolve } from 'node:path';
import { VideoPrefetchRunner } from './video-prefetch.runner';
import { VideoPrefetchStore } from './video-prefetch.store';
import {
  VideoPrefetchProvider,
  VideoPrefetchTaskGroup,
} from './video-prefetch.types';

class FakeProvider implements VideoPrefetchProvider {
  submitCount = 0;
  describeCount = 0;

  constructor(
    private readonly observations: VideoPrefetchTaskGroup[][],
    private readonly submitError?: Error,
  ) {}

  submit(): Promise<{ taskIds: string[]; requestId?: string }> {
    this.submitCount += 1;
    if (this.submitError) return Promise.reject(this.submitError);
    return Promise.resolve({ taskIds: [`task-${this.submitCount}`] });
  }

  describe(): Promise<VideoPrefetchTaskGroup[]> {
    const observation =
      this.observations[
        Math.min(this.describeCount, this.observations.length - 1)
      ];
    this.describeCount += 1;
    return Promise.resolve(observation);
  }
}

function groups(status: string, taskId = 'task-1'): VideoPrefetchTaskGroup[] {
  return [
    {
      taskId,
      tasks: [
        {
          taskId,
          url: 'https://cdn.example.com/video/v2/01.mp4',
          status,
          process: status === 'Complete' ? '100%' : '0%',
        },
      ],
    },
  ];
}

describe('VideoPrefetchRunner', () => {
  let directory: string;

  beforeEach(async () => {
    directory = await mkdtemp(resolve(tmpdir(), 'video-prefetch-test-'));
  });

  afterEach(async () => {
    await rm(directory, { recursive: true, force: true });
  });

  it('submits once and skips an already completed idempotent event', async () => {
    const provider = new FakeProvider([groups('Complete')]);
    const runner = new VideoPrefetchRunner({
      provider,
      store: new VideoPrefetchStore(directory),
    });
    const input = {
      trigger: 'CAMP_LAUNCH' as const,
      eventId: 'camp-2026-08-a',
      manifestVersion: 'v2',
      urls: ['https://cdn.example.com/video/v2/01.mp4'],
      domainName: 'cdn.example.com',
      pollIntervalMilliseconds: 1,
      timeoutMilliseconds: 100,
    };

    expect((await runner.run(input)).status).toBe('COMPLETE');
    expect((await runner.run(input)).status).toBe('COMPLETE');
    expect(provider.submitCount).toBe(1);
  });

  it('continues polling the same task after a timeout without resubmitting', async () => {
    const firstProvider = new FakeProvider([groups('Refreshing')]);
    const input = {
      trigger: 'CDN_CHANGE' as const,
      eventId: 'cdn-change-42',
      manifestVersion: 'v2',
      urls: ['https://cdn.example.com/video/v2/01.mp4'],
      domainName: 'cdn.example.com',
      pollIntervalMilliseconds: 2,
      timeoutMilliseconds: 5,
    };
    await expect(
      new VideoPrefetchRunner({
        provider: firstProvider,
        store: new VideoPrefetchStore(directory),
      }).run(input),
    ).rejects.toThrow('查询超时');
    expect(firstProvider.submitCount).toBe(1);

    const resumedProvider = new FakeProvider([groups('Complete')]);
    const resumed = await new VideoPrefetchRunner({
      provider: resumedProvider,
      store: new VideoPrefetchStore(directory),
    }).run({ ...input, timeoutMilliseconds: 100 });
    expect(resumed.status).toBe('COMPLETE');
    expect(resumedProvider.submitCount).toBe(0);
  });

  it('records a 403 as failed and requires an explicit retry', async () => {
    const forbidden = Object.assign(new Error('User not authorized'), {
      code: 'Forbidden.RAM',
      statusCode: 403,
    });
    const input = {
      trigger: 'NEW_RELEASE' as const,
      eventId: 'v3',
      manifestVersion: 'v3',
      urls: ['https://cdn.example.com/video/v3/01.mp4'],
      domainName: 'cdn.example.com',
      pollIntervalMilliseconds: 1,
      timeoutMilliseconds: 100,
    };
    await expect(
      new VideoPrefetchRunner({
        provider: new FakeProvider([], forbidden),
        store: new VideoPrefetchStore(directory),
      }).run(input),
    ).rejects.toThrow('User not authorized');

    const successfulProvider = new FakeProvider([groups('Complete')]);
    await expect(
      new VideoPrefetchRunner({
        provider: successfulProvider,
        store: new VideoPrefetchStore(directory),
      }).run(input),
    ).rejects.toThrow('--retry-failed');
    expect(
      (
        await new VideoPrefetchRunner({
          provider: successfulProvider,
          store: new VideoPrefetchStore(directory),
        }).run({ ...input, retryFailed: true })
      ).status,
    ).toBe('COMPLETE');
  });
});
