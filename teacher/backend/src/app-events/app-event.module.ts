import { Module } from '@nestjs/common';
import { AuthModule } from '../auth/auth.module';
import { AppEventController } from './app-event.controller';
import { AppEventStoreModule } from './app-event-store.module';

@Module({
  imports: [AuthModule, AppEventStoreModule],
  controllers: [AppEventController],
})
export class AppEventModule {}
