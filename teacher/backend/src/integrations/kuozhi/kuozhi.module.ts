import { Module } from '@nestjs/common';
import { KuozhiDetailClient } from './kuozhi-detail.client';
import { KuozhiProgressRepository } from './kuozhi-progress.repository';
import { KuozhiService } from './kuozhi.service';

@Module({
  providers: [KuozhiDetailClient, KuozhiProgressRepository, KuozhiService],
  exports: [KuozhiProgressRepository, KuozhiService],
})
export class KuozhiModule {}
