export const ONBOARDING_GUIDES = [
  { guideCode: 'FIRST_LOGIN', guideVersion: 1 },
  { guideCode: 'MY_TIDE_OVERVIEW', guideVersion: 1 },
  { guideCode: 'SCORE_DETAILS', guideVersion: 1 },
  { guideCode: 'TASK_PATH', guideVersion: 1 },
  { guideCode: 'TASK_RESULT', guideVersion: 1 },
  { guideCode: 'MESSAGES_TICKETS', guideVersion: 1 },
  { guideCode: 'HELP_ROUTES', guideVersion: 1 },
  { guideCode: 'PERSONALIZED_TASK_FIRST', guideVersion: 1 },
] as const;

export type OnboardingGuideDefinition = (typeof ONBOARDING_GUIDES)[number];
export type OnboardingGuideCode = OnboardingGuideDefinition['guideCode'];
export type OnboardingGuideVersion = OnboardingGuideDefinition['guideVersion'];

export const ONBOARDING_GUIDE_CODES: readonly OnboardingGuideCode[] =
  ONBOARDING_GUIDES.map(({ guideCode }) => guideCode);

export const FIRST_LOGIN_GUIDE_CODE = ONBOARDING_GUIDES[0].guideCode;
export const FIRST_LOGIN_GUIDE_VERSION = ONBOARDING_GUIDES[0].guideVersion;

export const ONBOARDING_ACKNOWLEDGEMENT_OUTCOMES = [
  'COMPLETED',
  'SKIPPED',
] as const;

export type OnboardingAcknowledgementOutcome =
  (typeof ONBOARDING_ACKNOWLEDGEMENT_OUTCOMES)[number];

export type OnboardingStatus =
  OnboardingAcknowledgementOutcome | 'MIGRATED_EXISTING';

export interface OnboardingGuideStateResponse {
  guideCode: OnboardingGuideCode;
  guideVersion: OnboardingGuideVersion;
  required: boolean;
  status: OnboardingStatus | null;
  acknowledgedAt: string | null;
}

export interface OnboardingStateResponse extends OnboardingGuideStateResponse {
  guides: OnboardingGuideStateResponse[];
}
