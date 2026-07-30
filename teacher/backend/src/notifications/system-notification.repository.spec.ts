import type { PoolClient } from 'pg';
import type { DatabaseService } from '../platform/database/database.service';
import {
  publicationConfigHash,
  systemNotificationPublicationSchema,
} from './system-notification.config';
import { SystemNotificationRepository } from './system-notification.repository';

const publication = systemNotificationPublicationSchema.parse({
  configKey: 'maintenance:20260723',
  typeCode: 'SYSTEM_MAINTENANCE',
  title: 'Scheduled maintenance',
  body: 'My TIDE will be unavailable for about 15 minutes.',
  publishAt: '2026-07-23T18:00:00+08:00',
  audience: { all: true },
});

describe('SystemNotificationRepository', () => {
  it.each(['SCHEDULED', 'FAILED'] as const)(
    'does not reset an unchanged %s publication retry state',
    async (status) => {
      const query = jest.fn().mockResolvedValue({
        rows: [
          {
            publicationId: '92000000-0000-4000-8000-000000000001',
            configKey: publication.configKey,
            typeCode: publication.typeCode,
            title: publication.title,
            body: publication.body,
            audience: publication.audience,
            actionType: null,
            actionTarget: null,
            scheduledAt: new Date(publication.publishAt),
            expiresAt: null,
            status,
            configHash: publicationConfigHash(publication),
            attemptCount: 4,
          },
        ],
      });
      const database = {
        withTideTransaction: jest.fn(
          (callback: (client: PoolClient) => Promise<void>) =>
            callback({ query } as unknown as PoolClient),
        ),
      } as unknown as DatabaseService;

      await new SystemNotificationRepository(database).syncPublication(
        publication,
      );

      expect(query).toHaveBeenCalledTimes(1);
    },
  );
});
