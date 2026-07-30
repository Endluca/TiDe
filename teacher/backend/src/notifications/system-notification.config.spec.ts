import {
  publicationActionTarget,
  publicationConfigHash,
  systemNotificationPublicationSchema,
} from './system-notification.config';

const validPublication = {
  configKey: 'maintenance:20260723',
  typeCode: 'SYSTEM_MAINTENANCE',
  title: 'Scheduled maintenance',
  body: 'My TIDE will be unavailable for about 15 minutes.',
  publishAt: '2026-07-23T18:00:00+08:00',
  audience: { all: true as const },
  action: { type: 'MY_TIDE' as const },
};

describe('system notification configuration', () => {
  it('accepts a scheduled English publication and fixes its route', () => {
    const publication =
      systemNotificationPublicationSchema.parse(validPublication);

    expect(publication.cancelled).toBe(false);
    expect(publicationActionTarget(publication.action)).toBe('/');
    expect(publicationConfigHash(publication)).toHaveLength(64);
  });

  it('rejects Chinese content and timestamps without Beijing offset', () => {
    expect(() =>
      systemNotificationPublicationSchema.parse({
        ...validPublication,
        title: '系统维护',
      }),
    ).toThrow('系统通知一期只允许英文内容');

    expect(() =>
      systemNotificationPublicationSchema.parse({
        ...validPublication,
        publishAt: '2026-07-23T18:00:00Z',
      }),
    ).toThrow('发布时间必须使用带 +08:00 的北京时间');
  });

  it('rejects arbitrary audience fields and action routes', () => {
    expect(() =>
      systemNotificationPublicationSchema.parse({
        ...validPublication,
        audience: { sql: 'teacher_id is not null' },
      }),
    ).toThrow();

    expect(() =>
      systemNotificationPublicationSchema.parse({
        ...validPublication,
        action: { type: 'MY_TIDE', target: 'https://example.com' },
      }),
    ).toThrow();
  });

  it('rejects retired fixed-task codes in audiences and task actions', () => {
    for (const retiredCode of ['G00', 'G10']) {
      expect(() =>
        systemNotificationPublicationSchema.parse({
          ...validPublication,
          action: { type: 'TASK_DETAIL', taskCode: retiredCode },
        }),
      ).toThrow('固定成长任务只允许当前 G01-G09 编码');

      expect(() =>
        systemNotificationPublicationSchema.parse({
          ...validPublication,
          audience: {
            task: {
              taskCodes: [retiredCode],
              statuses: ['ASSIGNED'],
            },
          },
        }),
      ).toThrow('固定成长任务只允许当前 G01-G09 编码');
    }
  });
});
