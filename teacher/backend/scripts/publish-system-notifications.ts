import { NestFactory } from '@nestjs/core';
import { AppModule } from '../src/app.module';
import { SystemNotificationPublisher } from '../src/notifications/system-notification.publisher';

async function main(): Promise<void> {
  const application = await NestFactory.createApplicationContext(AppModule, {
    logger: ['error', 'warn', 'log'],
  });
  try {
    await application
      .get(SystemNotificationPublisher)
      .runOnce({ throwOnError: true });
  } finally {
    await application.close();
  }
}

void main();
