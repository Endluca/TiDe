import { buildGrowthStageAvailableNotification } from './growth-stage-notification.policy';

describe('growth stage notification policy', () => {
  it('uses one generic message without relying on an unconfirmed stage name', () => {
    expect(buildGrowthStageAvailableNotification('teacher-001', 2)).toEqual({
      teacherId: 'teacher-001',
      typeCode: 'GROWTH_STAGE_AVAILABLE',
      title: 'Your next growth stage is ready',
      body: 'A new set of required tasks is now available in your growth path. Complete them in the order that works best for you.',
      actionType: 'TASKS',
      actionTarget: '/path',
      dedupeKey: 'growth-stage-available:teacher-001:2',
      stageNumber: 2,
    });
  });
});
