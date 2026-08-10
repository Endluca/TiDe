import { IsIn, IsInt } from 'class-validator';
import {
  ONBOARDING_ACKNOWLEDGEMENT_OUTCOMES,
  ONBOARDING_GUIDE_CODES,
  type OnboardingAcknowledgementOutcome,
  type OnboardingGuideCode,
} from '../onboarding.models';

export class AcknowledgeOnboardingDto {
  @IsIn(ONBOARDING_GUIDE_CODES)
  guideCode: OnboardingGuideCode;

  @IsInt()
  guideVersion: number;

  @IsIn(ONBOARDING_ACKNOWLEDGEMENT_OUTCOMES)
  outcome: OnboardingAcknowledgementOutcome;
}
