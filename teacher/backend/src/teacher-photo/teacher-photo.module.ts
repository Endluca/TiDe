import { Module } from '@nestjs/common';
import { AuthModule } from '../auth/auth.module';
import { FileModule } from '../files/file.module';
import { AiGatewayModule } from '../integrations/ai/ai-gateway.module';
import { CreamBright04Processor } from './cream-bright-04.processor';
import { TeacherPhotoController } from './teacher-photo.controller';
import { TeacherPhotoRepository } from './teacher-photo.repository';
import { TeacherPhotoService } from './teacher-photo.service';
import { TeacherPhotoWorker } from './teacher-photo.worker';

@Module({
  imports: [AuthModule, FileModule, AiGatewayModule],
  controllers: [TeacherPhotoController],
  providers: [
    CreamBright04Processor,
    TeacherPhotoRepository,
    TeacherPhotoService,
    TeacherPhotoWorker,
  ],
})
export class TeacherPhotoModule {}
