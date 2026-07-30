import { Module } from '@nestjs/common';
import { AuthModule } from '../auth/auth.module';
import { FileModule } from '../files/file.module';
import { SupportTicketController } from './support-ticket.controller';
import { SupportTicketRepository } from './support-ticket.repository';
import { SupportTicketScheduler } from './support-ticket.scheduler';
import { SupportTicketService } from './support-ticket.service';

@Module({
  imports: [AuthModule, FileModule],
  controllers: [SupportTicketController],
  providers: [
    SupportTicketRepository,
    SupportTicketService,
    SupportTicketScheduler,
  ],
})
export class SupportTicketModule {}
