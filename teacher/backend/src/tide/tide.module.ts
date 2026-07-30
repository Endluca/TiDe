import { Module } from '@nestjs/common';
import { AuthModule } from '../auth/auth.module';
import { ShiwenReadModule } from '../integrations/shiwen/shiwen-read.module';
import { TideController } from './tide.controller';
import { TideRepository } from './tide.repository';
import { TideService } from './tide.service';

@Module({
  imports: [AuthModule, ShiwenReadModule],
  controllers: [TideController],
  providers: [TideRepository, TideService],
})
export class TideModule {}
