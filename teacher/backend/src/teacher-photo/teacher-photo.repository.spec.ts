import type { PoolClient } from 'pg';
import type { DatabaseService } from '../platform/database/database.service';
import { TeacherPhotoRepository } from './teacher-photo.repository';

describe('TeacherPhotoRepository', () => {
  it('keeps the legacy photo pipeline unreachable after G00 is retired', async () => {
    const queries: string[] = [];
    let queryCount = 0;
    const query = jest.fn((queryText: string) => {
      queries.push(queryText);
      queryCount += 1;
      return Promise.resolve({
        rowCount: queryCount === 1 ? 1 : 0,
        rows: [],
      });
    });
    const client = {
      query,
    } as unknown as PoolClient;
    const database = {
      withTideTransaction: jest.fn(
        (work: (transactionClient: PoolClient) => Promise<unknown>) =>
          work(client),
      ),
    } as unknown as DatabaseService;
    const repository = new TeacherPhotoRepository(database);

    await expect(
      repository.reserve({
        accountId: 'account-001',
        taskInstanceId: 'assignment-001',
        idempotencyKey: 'photo-key-001',
        requestHash: 'a'.repeat(64),
        originalFileId: 'file-001',
        storageProvider: 'LOCAL',
        originalObjectKey: 'teacher-photo/file-001.jpg',
        originalFilename: 'camera.jpg',
        mimeType: 'image/jpeg',
        sizeBytes: 1024,
        sha256: 'b'.repeat(64),
        criteriaVersion: 'criteria-v1',
      }),
    ).resolves.toEqual({ type: 'TASK_NOT_FOUND' });

    const authorizationSql = queries[1] ?? '';
    expect(authorizationSql).toContain("task.task_code = 'G00'");
    expect(authorizationSql).toContain("execution.status = 'ACTIVE'");
    expect(authorizationSql).not.toContain("task.task_code = 'G04'");
  });

  it('serializes check arrays as JSON before writing jsonb columns', async () => {
    const client = {
      query: jest.fn().mockResolvedValue({ rowCount: 1, rows: [] }),
    } as unknown as PoolClient;
    const database = {
      withTideTransaction: jest.fn(
        (work: (transactionClient: PoolClient) => Promise<unknown>) =>
          work(client),
      ),
    } as unknown as DatabaseService;
    const repository = new TeacherPhotoRepository(database);
    const checks = [
      {
        id: 'camera_angle',
        title: '摄像头角度',
        status: 'uncertain' as const,
        message: '自动检测服务暂时不可用',
        suggestion: '请保持镜头与眼睛平齐',
      },
    ];
    const confidenceSummary = { errorCode: 'AI_GATEWAY_REJECTED' };

    await repository.review({
      photoRunId: 'photo-run',
      processingOwner: 'worker-001',
      aiRunId: 'ai-run',
      decision: 'ERROR',
      teacherMessage: '请稍后重新检测',
      checks,
      confidenceSummary,
    });

    const [sql, values] = (client.query as jest.Mock).mock.calls[0] as [
      string,
      unknown[],
    ];
    expect(sql).toContain('checks = $5::jsonb');
    expect(values[4]).toBe(JSON.stringify(checks));
    expect(values[5]).toBe(JSON.stringify(confidenceSummary));
    expect(JSON.parse(values[4] as string)).toEqual(checks);
  });

  it('claims pending photos atomically with skip-locked leases', async () => {
    const client = {
      query: jest
        .fn()
        .mockResolvedValueOnce({
          rowCount: 1,
          rows: [{ id: '00000000-0000-4000-8000-000000000001' }],
        })
        .mockResolvedValueOnce({
          rowCount: 1,
          rows: [
            {
              photoRunId: 'photo-run',
              taskInstanceId: 'assignment-001',
              accountId: 'account-001',
              originalFileId: 'original-file-001',
              originalStorageProvider: 'OSS',
              originalObjectKey: 'original.jpg',
              originalFilename: 'camera.jpg',
              originalMimeType: 'image/jpeg',
              finalFileId: null,
              finalStorageProvider: null,
              finalObjectKey: null,
              status: 'CHECKING',
              decision: null,
              criteriaVersion: 'criteria-v1',
              teacherMessage: null,
              checks: [],
              filterPreset: null,
              filterStrength: null,
              submittedAt: new Date('2026-07-29T00:00:00Z'),
              checkedAt: null,
              processedAt: null,
              requestHash: 'a'.repeat(64),
              processingOwner: 'worker-001',
              leaseExpiresAt: new Date('2026-07-29T00:02:00Z'),
              attemptCount: 1,
              nextAttemptAt: new Date('2026-07-29T00:00:00Z'),
            },
          ],
        }),
    } as unknown as PoolClient;
    const database = {
      withTideTransaction: jest.fn(
        (work: (transactionClient: PoolClient) => Promise<unknown>) =>
          work(client),
      ),
    } as unknown as DatabaseService;
    const repository = new TeacherPhotoRepository(database);

    await expect(
      repository.claimPending('worker-001', 2, 120_000),
    ).resolves.toMatchObject([
      {
        photoRunId: 'photo-run',
        processingOwner: 'worker-001',
        attemptCount: 1,
      },
    ]);

    const calls = (client.query as jest.Mock).mock.calls as unknown as Array<
      [string, unknown[]]
    >;
    const claimSql = String(calls[0][0]);
    expect(claimSql).toContain('FOR UPDATE SKIP LOCKED');
    expect(claimSql).toContain('lease_expires_at <= now()');
    expect(claimSql).toContain('attempt_count = attempt_count + 1');
    expect(calls[0][1]).toEqual(['worker-001', 2, 120_000]);
  });

  it('renews only unexpired row leases still owned by this pod', async () => {
    const queryTide = jest.fn().mockResolvedValue({
      rowCount: 1,
      rows: [{ id: '00000000-0000-4000-8000-000000000001' }],
    });
    const database = { queryTide } as unknown as DatabaseService;
    const repository = new TeacherPhotoRepository(database);

    await expect(
      repository.renewClaims(
        'worker-001',
        [
          '00000000-0000-4000-8000-000000000001',
          '00000000-0000-4000-8000-000000000002',
        ],
        120_000,
      ),
    ).resolves.toEqual(['00000000-0000-4000-8000-000000000001']);

    const [sql, values] = queryTide.mock.calls[0] as [string, unknown[]];
    expect(sql).toContain('processing_owner = $1');
    expect(sql).toContain('id = ANY($2::uuid[])');
    expect(sql).toContain('lease_expires_at > now()');
    expect(sql).toContain("status IN ('CHECKING', 'BEAUTIFYING')");
    expect(values).toEqual([
      'worker-001',
      [
        '00000000-0000-4000-8000-000000000001',
        '00000000-0000-4000-8000-000000000002',
      ],
      120_000,
    ]);
  });
});
