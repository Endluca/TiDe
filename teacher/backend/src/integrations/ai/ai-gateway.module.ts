import { Module } from '@nestjs/common';
import { AiGatewayService } from './ai-gateway.service';
import { AiRunRepository } from './ai-run.repository';
import { BytePlusModelArkClient } from './byteplus-modelark.client';

@Module({
  providers: [BytePlusModelArkClient, AiRunRepository, AiGatewayService],
  exports: [AiGatewayService],
})
export class AiGatewayModule {}
