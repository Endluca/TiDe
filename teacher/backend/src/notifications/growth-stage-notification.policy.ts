export type GrowthStageNumber = 1 | 2 | 3;

export interface GrowthStageAvailableNotification {
  teacherId: string;
  typeCode: 'GROWTH_STAGE_AVAILABLE';
  title: string;
  body: string;
  actionType: 'TASKS';
  actionTarget: '/path';
  dedupeKey: string;
  stageNumber: GrowthStageNumber;
}

export function buildGrowthStageAvailableNotification(
  teacherId: string,
  stageNumber: GrowthStageNumber,
): GrowthStageAvailableNotification {
  return {
    teacherId,
    typeCode: 'GROWTH_STAGE_AVAILABLE',
    title: 'Your next growth stage is ready',
    body: 'A new set of required tasks is now available in your growth path. Work through them in the order that works best for you.',
    actionType: 'TASKS',
    actionTarget: '/path',
    dedupeKey: `growth-stage-available:${teacherId}:${stageNumber}`,
    stageNumber,
  };
}
