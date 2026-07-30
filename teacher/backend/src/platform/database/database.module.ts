import { Global, Module } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import type { Pool } from 'pg';
import type { AppEnvironment } from '../config/environment';
import {
  SHIWEN_READ_DATABASE_POOL,
  TIDE_DATABASE_POOL,
} from './database.constants';
import { createShiwenReadPool, createTidePool } from './database.providers';
import { DatabaseService } from './database.service';
import { JobLeaseService } from './job-lease.service';

@Global()
@Module({
  providers: [
    {
      provide: TIDE_DATABASE_POOL,
      inject: [ConfigService],
      useFactory: (config: ConfigService<AppEnvironment, true>): Pool | null =>
        createTidePool(config),
    },
    {
      provide: SHIWEN_READ_DATABASE_POOL,
      inject: [ConfigService],
      useFactory: (config: ConfigService<AppEnvironment, true>): Pool | null =>
        createShiwenReadPool(config),
    },
    DatabaseService,
    JobLeaseService,
  ],
  exports: [DatabaseService, JobLeaseService],
})
export class DatabaseModule {}
