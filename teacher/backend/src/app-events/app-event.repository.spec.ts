import type { PoolClient } from 'pg';
import type { DatabaseService } from '../platform/database/database.service';
import type { CreateAppEventDto } from './dto/create-app-event.dto';
import { AppEventRepository } from './app-event.repository';

function event(eventId: string): CreateAppEventDto {
  return {
    eventName: 'TASK_CARD_CLICKED',
    eventId,
    eventSchemaVersion: 1,
    sessionId: 'browser-session-001',
    taskAssignmentId: 'assignment-001',
    properties: { stepKey: 'video' },
    occurredAt: '2026-07-29T00:00:00.000Z',
  };
}

describe('AppEventRepository batch writes', () => {
  it('loads binding and task context once before one bulk insert', async () => {
    const query = jest
      .fn()
      .mockResolvedValueOnce({
        rowCount: 1,
        rows: [{ bindingId: 'binding-001', teacherId: 'teacher-001' }],
      })
      .mockResolvedValueOnce({
        rowCount: 1,
        rows: [
          {
            taskAssignmentId: 'assignment-001',
            taskCode: 'G09',
            taskType: 'FIXED_GROWTH',
            templateVersion: 3,
            executionContractVersion: 'contract-v1',
            taskStatus: 'IN_PROGRESS',
            attemptNo: 1,
            stepKey: 'video',
            stepType: 'VIDEO',
          },
        ],
      })
      .mockResolvedValueOnce({ rowCount: 2, rows: [] });
    const database = {
      withTideTransaction: jest.fn(
        (work: (client: PoolClient) => Promise<unknown>) =>
          work({ query } as unknown as PoolClient),
      ),
    } as unknown as DatabaseService;
    const repository = new AppEventRepository(database);

    await expect(
      repository.saveClientBatch('account-001', 'authenticated-session-001', [
        { index: 0, input: event('event-batch-0001') },
        { index: 1, input: event('event-batch-0002') },
      ]),
    ).resolves.toEqual({
      acceptedEventIds: ['event-batch-0001', 'event-batch-0002'],
      ownershipFailureIndexes: [],
    });

    expect(query).toHaveBeenCalledTimes(3);
    const calls = query.mock.calls as unknown as Array<[string, unknown[]]>;
    expect(calls[1][0]).toContain(
      'assignment.assignment_id = ANY($1::varchar[])',
    );
    expect(calls[2][0]).toContain('FROM unnest(');
    expect(calls[2][0]).toContain('ON CONFLICT DO NOTHING');
  });
});
