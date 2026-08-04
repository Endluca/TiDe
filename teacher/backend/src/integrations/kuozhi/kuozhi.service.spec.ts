import { resolve } from 'node:path';
import { ConfigService } from '@nestjs/config';
import type { AppEnvironment } from '../../platform/config/environment';
import type { KuozhiDetailClient } from './kuozhi-detail.client';
import { KuozhiService } from './kuozhi.service';

function serviceFor(overrides: Partial<AppEnvironment> = {}) {
  const values: Partial<AppEnvironment> = {
    KUOZHI_LOGIN_URL: 'https://edu.51talk.com/login/ticket',
    KUOZHI_COURSE_URL: 'https://edu.51talk.com',
    KUOZHI_APP_KEY: 'test-app-key',
    KUOZHI_SECRET_KEY: 'test-secret-key',
    KUOZHI_COURSE_CONFIG_PATH: resolve(
      process.cwd(),
      'config/kuozhi-courses.json',
    ),
    KUOZHI_SAMPLE_MODE: false,
    ...overrides,
  };
  const config = {
    get: (key: keyof AppEnvironment) => values[key],
  } as ConfigService<AppEnvironment, true>;
  return new KuozhiService(config, {} as KuozhiDetailClient);
}

describe('KuozhiService', () => {
  it('keeps every confirmed formal course, task, and testpaper id', async () => {
    const service = serviceFor();
    const mappings = await Promise.all(
      ['G01', 'G02', 'G05', 'G06', 'G07', 'G08'].map(
        async (taskCode) =>
          [
            taskCode,
            (await service.resolveMapping(taskCode, 'TEACHER-001')).mapping,
          ] as const,
      ),
    );
    const compact = Object.fromEntries(
      mappings.map(([taskCode, mapping]) => [
        taskCode,
        {
          integrationStatus: mapping.integrationStatus,
          launchEnabled: mapping.launchEnabled,
          completionEnabled: mapping.completionEnabled,
          courses: mapping.courses.map((course) => ({
            courseId: course.courseId,
            tasks: course.tasks.map((task) => ({
              courseTaskId: task.courseTaskId,
              testpaperId:
                task.type === 'TESTPAPER' ? (task.testpaperId ?? null) : null,
            })),
          })),
        },
      ]),
    );

    expect(compact).toEqual({
      G01: {
        integrationStatus: 'MAPPING_ONLY',
        launchEnabled: false,
        completionEnabled: false,
        courses: [
          {
            courseId: '407',
            tasks: [{ courseTaskId: '2158', testpaperId: '305' }],
          },
        ],
      },
      G02: {
        integrationStatus: 'ACTIVE',
        launchEnabled: true,
        completionEnabled: true,
        courses: [
          {
            courseId: '499',
            tasks: [
              { courseTaskId: '2702', testpaperId: null },
              { courseTaskId: '2715', testpaperId: '415' },
            ],
          },
        ],
      },
      G05: {
        integrationStatus: 'ACTIVE',
        launchEnabled: true,
        completionEnabled: true,
        courses: [
          {
            courseId: '513',
            tasks: [{ courseTaskId: '2759', testpaperId: null }],
          },
        ],
      },
      G06: {
        integrationStatus: 'ACTIVE',
        launchEnabled: true,
        completionEnabled: true,
        courses: [
          {
            courseId: '520',
            tasks: [
              { courseTaskId: '2791', testpaperId: null },
              { courseTaskId: '2792', testpaperId: '449' },
            ],
          },
          {
            courseId: '398',
            tasks: [
              { courseTaskId: '1948', testpaperId: null },
              { courseTaskId: '1949', testpaperId: null },
              { courseTaskId: '1950', testpaperId: null },
              { courseTaskId: '1951', testpaperId: null },
              { courseTaskId: '1952', testpaperId: null },
              { courseTaskId: '1953', testpaperId: '239' },
              { courseTaskId: '1954', testpaperId: '240' },
              { courseTaskId: '1955', testpaperId: '241' },
              { courseTaskId: '1956', testpaperId: '242' },
              { courseTaskId: '1957', testpaperId: '243' },
            ],
          },
        ],
      },
      G07: {
        integrationStatus: 'PARTIAL',
        launchEnabled: true,
        completionEnabled: false,
        courses: [
          {
            courseId: '595',
            tasks: [
              { courseTaskId: '3164', testpaperId: null },
              { courseTaskId: '3163', testpaperId: null },
            ],
          },
        ],
      },
      G08: {
        integrationStatus: 'ACTIVE',
        launchEnabled: true,
        completionEnabled: true,
        courses: [
          {
            courseId: '630',
            tasks: [
              { courseTaskId: '3484', testpaperId: null },
              { courseTaskId: '3476', testpaperId: null },
              { courseTaskId: '3478', testpaperId: null },
              { courseTaskId: '3475', testpaperId: null },
              { courseTaskId: '3480', testpaperId: null },
              { courseTaskId: '3479', testpaperId: null },
              { courseTaskId: '3490', testpaperId: null },
              { courseTaskId: '3477', testpaperId: '562' },
            ],
          },
        ],
      },
    });
  });

  it('loads all formal G06 courses and creates new-window launch URLs', async () => {
    const response = await serviceFor().createLaunch('G06', 'TEACHER-001');

    expect(response).toMatchObject({
      provider: 'KUOZHI',
      dataMode: 'REAL',
      mappingVersion: 2,
      integrationStatus: 'ACTIVE',
    });
    expect(response.courses.map((course) => course.courseId)).toEqual([
      '520',
      '398',
    ]);
    const launch = new URL(response.courses[0].launchUrl);
    expect(launch.searchParams.get('id')).toBe('TEACHER-001');
    expect(launch.searchParams.get('to')).toBe(
      'https://edu.51talk.com/course/520?noheader=1',
    );
  });

  it('overrides only G06 with the isolated sample profile', async () => {
    const service = serviceFor({
      KUOZHI_SAMPLE_MODE: true,
      KUOZHI_SAMPLE_TEACHER_ID: '360107609',
    });
    const response = await service.createLaunch('G06', 'REAL-TEACHER');
    const launch = new URL(response.courses[0].launchUrl);

    expect(response.dataMode).toBe('SAMPLE_DRY_RUN');
    expect(response.courses).toHaveLength(1);
    expect(response.courses[0].courseId).toBe('131');
    expect(response.courses[0].embedMode).toBe('IFRAME');
    expect(launch.searchParams.get('id')).toBe('REAL-TEACHER');
    expect(launch.searchParams.get('to')).toBe(
      'https://edu.51talk.com/course/131?noheader=1',
    );
  });
});
