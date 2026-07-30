import { Module } from '@nestjs/common';
import { GrowthStageNotificationRepository } from './growth-stage-notification.repository';
import { GrowthStageNotificationScheduler } from './growth-stage-notification.scheduler';
import { PersonalizedTaskNotificationRepository } from './personalized-task-notification.repository';
import { PersonalizedTaskNotificationScheduler } from './personalized-task-notification.scheduler';
import { SystemNotificationPublisher } from './system-notification.publisher';
import { SystemNotificationRepository } from './system-notification.repository';

@Module({
  providers: [
    SystemNotificationRepository,
    SystemNotificationPublisher,
    PersonalizedTaskNotificationRepository,
    PersonalizedTaskNotificationScheduler,
    GrowthStageNotificationRepository,
    GrowthStageNotificationScheduler,
  ],
  exports: [
    SystemNotificationRepository,
    PersonalizedTaskNotificationRepository,
    GrowthStageNotificationRepository,
  ],
})
export class SystemNotificationModule {}
