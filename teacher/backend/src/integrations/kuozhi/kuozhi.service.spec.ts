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
      ['G01', 'G03', 'G05', 'G06', 'G07', 'G08', 'G09', 'P-REL-ATTENDANCE'].map(
        async (taskCode) =>
          [
            taskCode,
            await service.resolveMapping(taskCode, 'TEACHER-001'),
          ] as const,
      ),
    );
    const compact = Object.fromEntries(
      mappings.map(([taskCode, resolved]) => [
        taskCode,
        {
          mappingVersion: resolved.mappingVersion,
          integrationStatus: resolved.mapping.integrationStatus,
          launchEnabled: resolved.mapping.launchEnabled,
          completionEnabled: resolved.mapping.completionEnabled,
          autoCompleteAssignment: resolved.mapping.autoCompleteAssignment,
          courses: resolved.mapping.courses.map((course) => ({
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
        mappingVersion: 6,
        integrationStatus: 'ACTIVE',
        launchEnabled: true,
        completionEnabled: true,
        autoCompleteAssignment: false,
        courses: [
          {
            courseId: '407',
            tasks: [{ courseTaskId: '2158', testpaperId: '305' }],
          },
        ],
      },
      G03: {
        mappingVersion: 6,
        integrationStatus: 'ACTIVE',
        launchEnabled: true,
        completionEnabled: true,
        autoCompleteAssignment: true,
        courses: [
          {
            courseId: '655',
            tasks: [
              { courseTaskId: '3781', testpaperId: null },
              { courseTaskId: '3783', testpaperId: '582' },
              { courseTaskId: '3784', testpaperId: null },
              { courseTaskId: '3785', testpaperId: '583' },
              { courseTaskId: '3786', testpaperId: null },
              { courseTaskId: '3787', testpaperId: '584' },
            ],
          },
        ],
      },
      G05: {
        mappingVersion: 7,
        integrationStatus: 'ACTIVE',
        launchEnabled: true,
        completionEnabled: true,
        autoCompleteAssignment: true,
        courses: [
          {
            courseId: '657',
            tasks: [
              { courseTaskId: '3794', testpaperId: null },
              { courseTaskId: '3795', testpaperId: '585' },
            ],
          },
        ],
      },
      G06: {
        mappingVersion: 6,
        integrationStatus: 'ACTIVE',
        launchEnabled: true,
        completionEnabled: true,
        autoCompleteAssignment: true,
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
        mappingVersion: 6,
        integrationStatus: 'PARTIAL',
        launchEnabled: true,
        completionEnabled: false,
        autoCompleteAssignment: true,
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
        mappingVersion: 7,
        integrationStatus: 'ACTIVE',
        launchEnabled: true,
        completionEnabled: true,
        autoCompleteAssignment: true,
        courses: [
          {
            courseId: '656',
            tasks: [
              { courseTaskId: '3788', testpaperId: null },
              { courseTaskId: '3789', testpaperId: null },
              { courseTaskId: '3791', testpaperId: null },
              { courseTaskId: '3792', testpaperId: null },
              { courseTaskId: '3793', testpaperId: null },
              { courseTaskId: '3790', testpaperId: null },
            ],
          },
        ],
      },
      G09: {
        mappingVersion: 8,
        integrationStatus: 'ACTIVE',
        launchEnabled: true,
        completionEnabled: true,
        autoCompleteAssignment: true,
        courses: [
          {
            courseId: '658',
            tasks: [
              { courseTaskId: '3826', testpaperId: null },
              { courseTaskId: '3829', testpaperId: '589' },
              { courseTaskId: '3831', testpaperId: null },
              { courseTaskId: '3832', testpaperId: '590' },
              { courseTaskId: '3836', testpaperId: null },
              { courseTaskId: '3834', testpaperId: '591' },
            ],
          },
        ],
      },
      'P-REL-ATTENDANCE': {
        mappingVersion: 1,
        integrationStatus: 'PARTIAL',
        launchEnabled: true,
        completionEnabled: false,
        autoCompleteAssignment: false,
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
    });
  });

  it('does not expose the retired G02 Kuozhi course mapping', async () => {
    await expect(
      serviceFor().resolveMapping('G02', 'TEACHER-001'),
    ).rejects.toMatchObject({
      response: {
        code: 'KUOZHI_COURSE_NOT_CONFIGURED',
      },
    });
  });

  it('loads all formal G06 courses as iframe-only launch URLs', async () => {
    const response = await serviceFor().createLaunch('G06', 'TEACHER-001');

    expect(response).toMatchObject({
      provider: 'KUOZHI',
      dataMode: 'REAL',
      mappingVersion: 6,
      integrationStatus: 'ACTIVE',
    });
    expect(response.courses.map((course) => course.courseId)).toEqual([
      '520',
      '398',
    ]);
    expect(
      response.courses.every((course) => course.embedMode === 'IFRAME'),
    ).toBe(true);
    const launch = new URL(response.courses[0].launchUrl);
    expect(launch.searchParams.get('id')).toBe('TEACHER-001');
    expect(launch.searchParams.get('to')).toBe(
      'https://edu.51talk.com/course/520?noheader=1',
    );
  });

  it('opens G01 course 407 as an embedded Kuozhi course', async () => {
    const response = await serviceFor().createLaunch('G01', 'TEACHER-001');

    expect(response).toMatchObject({
      integrationStatus: 'ACTIVE',
      mappingVersion: 6,
      courses: [{ courseId: '407', embedMode: 'IFRAME' }],
    });
  });

  it('opens G09 course 658 as an embedded Kuozhi course', async () => {
    const response = await serviceFor().createLaunch('G09', 'TEACHER-001');

    expect(response).toMatchObject({
      integrationStatus: 'ACTIVE',
      mappingVersion: 8,
      courses: [{ courseId: '658', embedMode: 'IFRAME' }],
    });
  });

  it('opens personalized attendance training on course 595 without enabling incomplete completion evidence', async () => {
    const response = await serviceFor().createLaunch(
      'P-REL-ATTENDANCE',
      'TEACHER-001',
    );

    expect(response).toMatchObject({
      integrationStatus: 'PARTIAL',
      mappingVersion: 1,
      courses: [{ courseId: '595', embedMode: 'IFRAME' }],
    });
  });
});
