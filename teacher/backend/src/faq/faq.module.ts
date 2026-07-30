import { Module } from '@nestjs/common';
import { AppEventStoreModule } from '../app-events/app-event-store.module';
import { AuthModule } from '../auth/auth.module';
import { AiGatewayModule } from '../integrations/ai/ai-gateway.module';
import { FaqController } from './faq.controller';
import { FaqRepository } from './faq.repository';
import { FaqRetriever } from './faq-retriever';
import { FaqService } from './faq.service';

@Module({
  imports: [AuthModule, AiGatewayModule, AppEventStoreModule],
  controllers: [FaqController],
  providers: [FaqRepository, FaqRetriever, FaqService],
})
export class FaqModule {}
