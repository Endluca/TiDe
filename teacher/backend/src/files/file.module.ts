import { Module } from '@nestjs/common';
import { AuthModule } from '../auth/auth.module';
import { FileController } from './file.controller';
import { FileRepository } from './file.repository';
import { FileService } from './file.service';
import { FileStorageAdapter } from './file-storage.adapter';
import { FileStorageRouter } from './file-storage.router';
import { LocalFileStorageAdapter } from './local-file-storage.adapter';
import { OssFileStorageAdapter } from './oss-file-storage.adapter';

@Module({
  imports: [AuthModule],
  controllers: [FileController],
  providers: [
    FileRepository,
    FileService,
    LocalFileStorageAdapter,
    OssFileStorageAdapter,
    FileStorageRouter,
    {
      provide: FileStorageAdapter,
      useExisting: FileStorageRouter,
    },
  ],
  exports: [FileStorageAdapter],
})
export class FileModule {}
