import {
  BadRequestException,
  ConflictException,
  Injectable,
  UnprocessableEntityException,
} from '@nestjs/common';
import { createHash } from 'node:crypto';
import type { AuthPrincipal } from '../auth/auth.models';
import type { AcknowledgeOnboardingDto } from './dto/acknowledge-onboarding.dto';
import {
  ONBOARDING_GUIDE_CODES,
  ONBOARDING_GUIDES,
  type OnboardingGuideDefinition,
  type OnboardingGuideStateResponse,
  type OnboardingStateResponse,
} from './onboarding.models';
import {
  OnboardingIdempotencyConflictError,
  OnboardingRepository,
  type OnboardingStateRecord,
} from './onboarding.repository';

@Injectable()
export class OnboardingService {
  constructor(private readonly repository: OnboardingRepository) {}

  async getState(principal: AuthPrincipal): Promise<OnboardingStateResponse> {
    const states = await this.repository.findStates(principal.accountId);
    const statesByGuide = new Map(
      states.map((state) => [this.guideKey(state), state]),
    );
    const guides = ONBOARDING_GUIDES.map((guide) =>
      this.response(guide, statesByGuide.get(this.guideKey(guide)) ?? null),
    );
    return {
      ...guides[0],
      guides,
    };
  }

  async acknowledge(
    principal: AuthPrincipal,
    idempotencyKeyValue: string | undefined,
    input: AcknowledgeOnboardingDto,
  ): Promise<OnboardingGuideStateResponse> {
    const guide = this.requireSupportedGuide(
      input.guideCode,
      input.guideVersion,
    );
    const idempotencyKey = this.requireIdempotencyKey(idempotencyKeyValue);
    const requestHash = createHash('sha256')
      .update(
        JSON.stringify({
          guideCode: guide.guideCode,
          guideVersion: input.guideVersion,
          outcome: input.outcome,
        }),
      )
      .digest('hex');

    try {
      const state = await this.repository.acknowledge({
        accountId: principal.accountId,
        guideCode: guide.guideCode,
        guideVersion: input.guideVersion,
        outcome: input.outcome,
        idempotencyKey,
        requestHash,
      });
      return this.response(guide, state);
    } catch (error) {
      if (error instanceof OnboardingIdempotencyConflictError) {
        throw new ConflictException({
          code: 'IDEMPOTENCY_KEY_REUSED',
          message: '该幂等键已用于其他引导确认请求',
          retryable: false,
        });
      }
      throw error;
    }
  }

  private response(
    guide: OnboardingGuideDefinition,
    state: OnboardingStateRecord | null,
  ): OnboardingGuideStateResponse {
    return {
      guideCode: guide.guideCode,
      guideVersion: guide.guideVersion,
      required: state === null,
      status: state?.status ?? null,
      acknowledgedAt: state?.acknowledgedAt.toISOString() ?? null,
    };
  }

  private requireSupportedGuide(
    guideCode: string,
    guideVersion: number,
  ): OnboardingGuideDefinition {
    const guide = ONBOARDING_GUIDES.find(
      (candidate) => candidate.guideCode === guideCode,
    );
    if (!guide) {
      throw new UnprocessableEntityException({
        code: 'ONBOARDING_GUIDE_UNSUPPORTED',
        message: '当前新手引导模块不受支持',
        retryable: false,
        details: { supportedGuideCodes: ONBOARDING_GUIDE_CODES },
      });
    }
    if (guideVersion !== guide.guideVersion) {
      throw new UnprocessableEntityException({
        code: 'ONBOARDING_VERSION_UNSUPPORTED',
        message: '当前新手引导版本不受支持',
        retryable: false,
        details: {
          guideCode: guide.guideCode,
          supportedVersion: guide.guideVersion,
        },
      });
    }
    return guide;
  }

  private guideKey(
    guide: Pick<OnboardingStateRecord, 'guideCode' | 'guideVersion'>,
  ): string {
    return `${guide.guideCode}:${guide.guideVersion}`;
  }

  private requireIdempotencyKey(value: string | undefined): string {
    const key = value?.trim();
    if (!key || key.length < 8 || key.length > 128) {
      throw new BadRequestException({
        code: 'INVALID_IDEMPOTENCY_KEY',
        message: 'Idempotency-Key 长度必须为 8 到 128 个字符',
        retryable: false,
      });
    }
    return key;
  }
}
