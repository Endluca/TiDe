import {
  BadRequestException,
  ConflictException,
  UnprocessableEntityException,
} from '@nestjs/common';
import type { AuthPrincipal } from '../auth/auth.models';
import type { AcknowledgeOnboardingDto } from './dto/acknowledge-onboarding.dto';
import { OnboardingService } from './onboarding.service';
import {
  OnboardingIdempotencyConflictError,
  type OnboardingRepository,
} from './onboarding.repository';

const principal: AuthPrincipal = {
  accountId: 'account-001',
  sessionId: 'session-001',
};
const acknowledgedAt = new Date('2026-08-07T08:00:00.000Z');

function fixture() {
  const findState = jest.fn();
  const findStates = jest.fn();
  const acknowledge = jest.fn();
  const repository = {
    findState,
    findStates,
    acknowledge,
  } as unknown as OnboardingRepository;
  return {
    service: new OnboardingService(repository),
    findState,
    findStates,
    acknowledge,
  };
}

describe('OnboardingService', () => {
  it('requires the current guide when the account has no acknowledgement', async () => {
    const { service, findStates } = fixture();
    findStates.mockResolvedValue([]);

    const response = await service.getState(principal);
    expect(response).toMatchObject({
      guideCode: 'FIRST_LOGIN',
      guideVersion: 1,
      required: true,
      status: null,
      acknowledgedAt: null,
    });
    expect(response.guides).toHaveLength(8);
    expect(response.guides.every((guide) => guide.required)).toBe(true);
    expect(findStates).toHaveBeenCalledWith('account-001');
  });

  it('lists every supported guide in stable product order', async () => {
    const { service, findStates } = fixture();
    findStates.mockResolvedValue([
      {
        guideCode: 'SCORE_DETAILS',
        guideVersion: 1,
        status: 'SKIPPED',
        acknowledgedAt,
      },
    ]);

    const response = await service.getState(principal);

    expect(response.guides.map((item) => item.guideCode)).toEqual([
      'FIRST_LOGIN',
      'MY_TIDE_OVERVIEW',
      'SCORE_DETAILS',
      'TASK_PATH',
      'TASK_RESULT',
      'MESSAGES_TICKETS',
      'HELP_ROUTES',
      'PERSONALIZED_TASK_FIRST',
    ]);
    expect(response.guides).toHaveLength(8);
    expect(response.guides[0]).toMatchObject({ required: true, status: null });
    expect(response.guides[2]).toEqual({
      guideCode: 'SCORE_DETAILS',
      guideVersion: 1,
      required: false,
      status: 'SKIPPED',
      acknowledgedAt: acknowledgedAt.toISOString(),
    });
    expect(findStates).toHaveBeenCalledWith('account-001');
  });

  it('returns an immutable completed, skipped or migrated state as not required', async () => {
    const { service, findStates } = fixture();
    findStates.mockResolvedValue([
      {
        guideCode: 'FIRST_LOGIN',
        guideVersion: 1,
        status: 'MIGRATED_EXISTING',
        acknowledgedAt,
      },
    ]);

    await expect(service.getState(principal)).resolves.toMatchObject({
      guideCode: 'FIRST_LOGIN',
      guideVersion: 1,
      required: false,
      status: 'MIGRATED_EXISTING',
      acknowledgedAt: acknowledgedAt.toISOString(),
    });
  });

  it('acknowledges a supported module only for the authenticated account', async () => {
    const { service, acknowledge } = fixture();
    acknowledge.mockResolvedValue({
      guideCode: 'TASK_RESULT',
      guideVersion: 1,
      status: 'COMPLETED',
      acknowledgedAt,
    });

    await expect(
      service.acknowledge(principal, ' onboarding-key-001 ', {
        guideCode: 'TASK_RESULT',
        guideVersion: 1,
        outcome: 'COMPLETED',
      }),
    ).resolves.toMatchObject({
      guideCode: 'TASK_RESULT',
      required: false,
      status: 'COMPLETED',
    });
    expect(acknowledge).toHaveBeenCalledTimes(1);
    const calls = acknowledge.mock.calls as unknown as Array<
      [Record<string, unknown>]
    >;
    expect(calls[0][0]).toMatchObject({
      accountId: 'account-001',
      guideCode: 'TASK_RESULT',
      guideVersion: 1,
      outcome: 'COMPLETED',
      idempotencyKey: 'onboarding-key-001',
    });
    expect(calls[0][0].requestHash).toMatch(/^[a-f0-9]{64}$/);
  });

  it('returns explicit errors for an unsupported version and invalid key', async () => {
    const { service, acknowledge } = fixture();

    try {
      await service.acknowledge(principal, 'onboarding-key-001', {
        guideCode: 'FIRST_LOGIN',
        guideVersion: 2,
        outcome: 'COMPLETED',
      });
      throw new Error('expected unsupported version to fail');
    } catch (error) {
      expect(error).toBeInstanceOf(UnprocessableEntityException);
      expect(
        (error as UnprocessableEntityException).getResponse(),
      ).toMatchObject({
        code: 'ONBOARDING_VERSION_UNSUPPORTED',
      });
    }
    await expect(
      service.acknowledge(principal, 'short', {
        guideCode: 'FIRST_LOGIN',
        guideVersion: 1,
        outcome: 'SKIPPED',
      }),
    ).rejects.toBeInstanceOf(BadRequestException);
    expect(acknowledge).not.toHaveBeenCalled();
  });

  it('maps reuse of one key for another payload to the public conflict code', async () => {
    const { service, acknowledge } = fixture();
    acknowledge.mockRejectedValue(new OnboardingIdempotencyConflictError());

    try {
      await service.acknowledge(principal, 'onboarding-key-001', {
        guideCode: 'FIRST_LOGIN',
        guideVersion: 1,
        outcome: 'SKIPPED',
      });
      throw new Error('expected acknowledgement to fail');
    } catch (error) {
      expect(error).toBeInstanceOf(ConflictException);
      expect((error as ConflictException).getResponse()).toMatchObject({
        code: 'IDEMPOTENCY_KEY_REUSED',
      });
    }
  });

  it('uses an unprocessable response for unsupported guide versions', async () => {
    const { service } = fixture();

    await expect(
      service.acknowledge(principal, 'onboarding-key-001', {
        guideCode: 'FIRST_LOGIN',
        guideVersion: 3,
        outcome: 'SKIPPED',
      }),
    ).rejects.toBeInstanceOf(UnprocessableEntityException);
  });

  it('rejects guide codes outside the reviewed module registry', async () => {
    const { service, acknowledge } = fixture();
    const unsupported = {
      guideCode: 'TASK_EXECUTION',
      guideVersion: 1,
      outcome: 'COMPLETED',
    } as unknown as AcknowledgeOnboardingDto;

    try {
      await service.acknowledge(principal, 'onboarding-key-001', unsupported);
      throw new Error('expected unsupported guide to fail');
    } catch (error) {
      expect(error).toBeInstanceOf(UnprocessableEntityException);
      expect(
        (error as UnprocessableEntityException).getResponse(),
      ).toMatchObject({
        code: 'ONBOARDING_GUIDE_UNSUPPORTED',
      });
    }
    expect(acknowledge).not.toHaveBeenCalled();
  });
});
