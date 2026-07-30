import { Module } from '@nestjs/common';
import { AppEventRepository } from './app-event.repository';
import { AppEventService } from './app-event.service';

@Module({
  providers: [AppEventRepository, AppEventService],
  exports: [AppEventRepository, AppEventService],
})
export class AppEventStoreModule {}
