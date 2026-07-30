import { Module } from '@nestjs/common';
import { PostgresShiwenReadAdapter } from './postgres-shiwen-read.adapter';
import { ShiwenTeacherReadAdapter } from './shiwen-read.adapters';

@Module({
  providers: [
    PostgresShiwenReadAdapter,
    {
      provide: ShiwenTeacherReadAdapter,
      useExisting: PostgresShiwenReadAdapter,
    },
  ],
  exports: [ShiwenTeacherReadAdapter],
})
export class ShiwenReadModule {}
