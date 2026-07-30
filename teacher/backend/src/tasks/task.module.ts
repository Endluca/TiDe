import { Module } from '@nestjs/common';
import { AppEventStoreModule } from '../app-events/app-event-store.module';
import { AuthModule } from '../auth/auth.module';
import { FileModule } from '../files/file.module';
import { AiGatewayModule } from '../integrations/ai/ai-gateway.module';
import { TaskController } from './task.controller';
import { TaskRepository } from './task.repository';
import { TaskService } from './task.service';
import { AllStepsCompleteRuleHandler } from './all-steps-complete-rule.handler';
import { TaskValidationEngine } from './task-validation.engine';
import { G01ExternalStatusRuleHandler } from './g01-external-status-rule.handler';
import { AiImageReviewRuleHandler } from './ai-image-review-rule.handler';
import { ImageReviewRepository } from './image-review.repository';

@Module({
  imports: [AuthModule, FileModule, AiGatewayModule, AppEventStoreModule],
  controllers: [TaskController],
  providers: [
    AllStepsCompleteRuleHandler,
    AiImageReviewRuleHandler,
    ImageReviewRepository,
    TaskValidationEngine,
    G01ExternalStatusRuleHandler,
    TaskRepository,
    TaskService,
  ],
})
export class TaskModule {}
