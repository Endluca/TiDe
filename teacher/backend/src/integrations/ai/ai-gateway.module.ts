import { Module } from '@nestjs/common';
import { AiGatewayService } from './ai-gateway.service';
import { AiRunRepository } from './ai-run.repository';
import { CompanyAiGatewayClient } from './company-ai-gateway.client';

@Module({
  providers: [CompanyAiGatewayClient, AiRunRepository, AiGatewayService],
  exports: [AiGatewayService],
})
export class AiGatewayModule {}
