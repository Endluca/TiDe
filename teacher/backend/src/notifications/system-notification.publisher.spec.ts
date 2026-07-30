import type { ConfigService } from '@nestjs/config';
import type { AppEnvironment } from '../platform/config/environment';
import type { SystemNotificationRepository } from './system-notification.repository';
import { SystemNotificationPublisher } from './system-notification.publisher';

describe('SystemNotificationPublisher', () => {
  it('stays off when the shared background-job switch is disabled', async () => {
    const config = {
      get: jest.fn((key: keyof AppEnvironment) => {
        if (key === 'BACKGROUND_JOBS_ENABLED') return false;
        if (key === 'SYSTEM_NOTIFICATION_PUBLISHER_ENABLED') return true;
        return undefined;
      }),
    } as unknown as ConfigService<AppEnvironment, true>;
    const repository = {
      syncPublication: jest.fn(),
      publishDue: jest.fn(),
    };
    const publisher = new SystemNotificationPublisher(
      config,
      repository as unknown as SystemNotificationRepository,
    );

    await publisher.onModuleInit();
    await publisher.runOnce({ throwOnError: true });

    expect(repository.syncPublication).not.toHaveBeenCalled();
    expect(repository.publishDue).not.toHaveBeenCalled();
  });
});
