import {
  BadRequestException,
  NotFoundException,
  PayloadTooLargeException,
} from '@nestjs/common';
import type { AuthPrincipal } from '../auth/auth.models';
import type { CreateAppEventDto } from './dto/create-app-event.dto';
import {
  AppEventOwnershipError,
  type AppEventRepository,
} from './app-event.repository';
import { AppEventService } from './app-event.service';

const principal: AuthPrincipal = {
  accountId: 'account-001',
  sessionId: 'authenticated-session-001',
};

function input(patch: Partial<CreateAppEventDto> = {}): CreateAppEventDto {
  return {
    eventName: 'PAGE_VIEWED',
    eventId: 'event-unit-0001',
    eventSchemaVersion: 1,
    sessionId: 'browser-session-001',
    properties: { page: '/my-tide', language: 'zh' },
    occurredAt: new Date().toISOString(),
    ...patch,
  };
}

function fixture() {
  const saveClient = jest.fn().mockResolvedValue({
    accepted: true,
    eventId: 'event-unit-0001',
  });
  const saveAnonymousClient = jest.fn().mockResolvedValue({
    accepted: true,
    eventId: 'event-unit-0001',
  });
  const saveSystem = jest.fn().mockResolvedValue({
    accepted: true,
    eventId: 'system-event-0001',
  });
  const saveClientBatch = jest.fn().mockResolvedValue({
    acceptedEventIds: ['event-unit-0001'],
    ownershipFailureIndexes: [],
  });
  const saveAnonymousClientBatch = jest
    .fn()
    .mockResolvedValue(['event-unit-0001']);
  const repository = {
    saveClient,
    saveAnonymousClient,
    saveClientBatch,
    saveAnonymousClientBatch,
    saveSystem,
  } as unknown as AppEventRepository;
  return {
    service: new AppEventService(repository),
    saveClient,
    saveAnonymousClient,
    saveClientBatch,
    saveAnonymousClientBatch,
    saveSystem,
  };
}

describe('AppEventService', () => {
  it('accepts a dictionary event and passes the authenticated owner context', async () => {
    const { service, saveClient } = fixture();

    await expect(service.save(principal, input())).resolves.toEqual({
      accepted: true,
      eventId: 'event-unit-0001',
    });
    expect(saveClient).toHaveBeenCalledWith(
      principal.accountId,
      principal.sessionId,
      expect.objectContaining({
        eventName: 'PAGE_VIEWED',
        sessionId: 'browser-session-001',
      }),
    );
  });

  it('accepts authenticated onboarding events and their flat guide properties', async () => {
    const { service, saveClient } = fixture();

    const names = [
      'ONBOARDING_SHOWN',
      'ONBOARDING_STEP_VIEWED',
      'ONBOARDING_COMPLETED',
      'ONBOARDING_SKIPPED',
      'ONBOARDING_REPLAYED',
    ];
    for (const [index, eventName] of names.entries()) {
      await expect(
        service.save(
          principal,
          input({
            eventName,
            eventId: `onboarding-event-${index + 1}`,
            properties: {
              guideCode: 'FIRST_LOGIN',
              guideVersion: 1,
              onboardingStep: 2,
            },
          }),
        ),
      ).resolves.toMatchObject({ accepted: true });
    }
    expect(saveClient).toHaveBeenCalledTimes(names.length);
  });

  it('rejects unknown, backend-only and task-less task events', async () => {
    const { service } = fixture();

    await expect(
      service.save(principal, input({ eventName: 'NOT_IN_DICTIONARY' })),
    ).rejects.toBeInstanceOf(BadRequestException);
    await expect(
      service.save(principal, input({ eventName: 'TASK_COMPLETED' })),
    ).rejects.toBeInstanceOf(BadRequestException);
    await expect(
      service.save(principal, input({ eventName: 'TASK_CARD_CLICKED' })),
    ).rejects.toBeInstanceOf(BadRequestException);
  });

  it('rejects sensitive, nested, overlong and oversized properties', async () => {
    const { service } = fixture();

    await expect(
      service.save(principal, input({ properties: { answer: 'A' } })),
    ).rejects.toBeInstanceOf(BadRequestException);
    await expect(
      service.save(principal, input({ properties: { page: { path: '/' } } })),
    ).rejects.toBeInstanceOf(BadRequestException);
    await expect(
      service.save(principal, input({ properties: { page: 'x'.repeat(513) } })),
    ).rejects.toBeInstanceOf(BadRequestException);

    const largeProperties = Object.fromEntries(
      [
        'page',
        'entrySource',
        'displayPosition',
        'sourcePage',
        'targetPage',
        'taskCode',
        'taskType',
        'executionContractVersion',
        'taskStatus',
        'stepKey',
        'stepType',
        'deviceType',
        'os',
        'browser',
        'language',
        'clientVersion',
        'result',
      ].map((key) => [key, 'x'.repeat(500)]),
    );
    await expect(
      service.save(principal, input({ properties: largeProperties })),
    ).rejects.toBeInstanceOf(PayloadTooLargeException);
  });

  it('maps an unowned task assignment to a non-disclosing not-found result', async () => {
    const { service, saveClient } = fixture();
    saveClient.mockRejectedValue(new AppEventOwnershipError());

    await expect(
      service.save(
        principal,
        input({
          eventName: 'TASK_CARD_CLICKED',
          taskAssignmentId: 'someone-elses-assignment',
        }),
      ),
    ).rejects.toBeInstanceOf(NotFoundException);
  });

  it('allows only the anonymous event subset without a task assignment', async () => {
    const { service, saveAnonymousClient } = fixture();

    await expect(service.saveAnonymous(input())).resolves.toMatchObject({
      accepted: true,
    });
    expect(saveAnonymousClient).toHaveBeenCalledTimes(1);
    expect(() =>
      service.saveAnonymous(
        input({
          eventName: 'TASK_CARD_CLICKED',
          taskAssignmentId: 'assignment-001',
        }),
      ),
    ).toThrow(BadRequestException);
  });

  it('drops an invalid best-effort backend event without touching business flow', () => {
    const { service, saveSystem } = fixture();

    expect(() =>
      service.captureSystem({
        eventName: 'TASK_COMPLETED',
        eventId: 'system-event-0001',
        sessionId: 'system-session-001',
        accountId: 'account-001',
        properties: { token: 'must-not-be-recorded' },
      }),
    ).not.toThrow();
    expect(saveSystem).not.toHaveBeenCalled();
  });

  it('accepts valid batch items and reports invalid indexes separately', async () => {
    const { service, saveClientBatch } = fixture();
    const valid = input();

    await expect(
      service.saveBatch(principal, [
        valid,
        input({ eventId: 'event-unit-0002', eventName: 'NOT_ALLOWED' }),
      ]),
    ).resolves.toEqual({
      acceptedEventIds: ['event-unit-0001'],
      failures: [{ index: 1, code: 'APP_EVENT_NOT_ALLOWED' }],
    });
    expect(saveClientBatch).toHaveBeenCalledWith(
      principal.accountId,
      principal.sessionId,
      [{ index: 0, input: valid }],
    );
  });

  it('batches the safe anonymous event subset', async () => {
    const { service, saveAnonymousClientBatch } = fixture();

    await expect(
      service.saveAnonymousBatch([
        input(),
        input({
          eventId: 'event-unit-0002',
          eventName: 'TASK_CARD_CLICKED',
          taskAssignmentId: 'assignment-001',
        }),
      ]),
    ).resolves.toEqual({
      acceptedEventIds: ['event-unit-0001'],
      failures: [{ index: 1, code: 'APP_EVENT_AUTH_REQUIRED' }],
    });
    expect(saveAnonymousClientBatch).toHaveBeenCalledTimes(1);
  });
});
