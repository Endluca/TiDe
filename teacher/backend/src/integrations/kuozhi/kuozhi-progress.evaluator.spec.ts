import type { KuozhiResolvedMapping } from './kuozhi.models';
import { evaluateKuozhiProgress } from './kuozhi-progress.evaluator';

const resolved: KuozhiResolvedMapping = {
  mappingVersion: 5,
  taskCode: 'G06',
  dataMode: 'REAL',
  queryTeacherId: 'TEACHER-001',
  mapping: {
    integrationStatus: 'ACTIVE',
    launchEnabled: true,
    completionEnabled: true,
    autoCompleteAssignment: true,
    noHeader: true,
    courses: [
      {
        courseId: '520',
        title: 'ME Culture and PARSNIP',
        tasks: [
          {
            courseTaskId: '2791',
            type: 'VIDEO',
            required: true,
            completionPercent: 100,
          },
          {
            courseTaskId: '2792',
            type: 'TESTPAPER',
            required: true,
          },
        ],
      },
    ],
  },
};

describe('evaluateKuozhiProgress', () => {
  it('completes G09 only after its three videos and three paired quizzes finish', () => {
    const g09: KuozhiResolvedMapping = {
      mappingVersion: 8,
      taskCode: 'G09',
      dataMode: 'REAL',
      queryTeacherId: 'TEACHER-001',
      mapping: {
        integrationStatus: 'ACTIVE',
        launchEnabled: true,
        completionEnabled: true,
        autoCompleteAssignment: true,
        noHeader: true,
        courses: [
          {
            courseId: '658',
            tasks: [
              {
                courseTaskId: '3826',
                type: 'VIDEO',
                required: true,
                completionPercent: 100,
              },
              { courseTaskId: '3829', type: 'TESTPAPER', required: true },
              {
                courseTaskId: '3831',
                type: 'VIDEO',
                required: true,
                completionPercent: 100,
              },
              { courseTaskId: '3832', type: 'TESTPAPER', required: true },
              {
                courseTaskId: '3836',
                type: 'VIDEO',
                required: true,
                completionPercent: 100,
              },
              { courseTaskId: '3834', type: 'TESTPAPER', required: true },
            ],
          },
        ],
      },
    };
    const complete = {
      id: '658',
      percent: 100,
      task_list: {
        '3826': { id: '3826', type: 'video', percent: 100 },
        '3829': { id: '3829', type: 'testpaper', percent: 100 },
        '3831': { id: '3831', type: 'video', percent: 100 },
        '3832': { id: '3832', type: 'testpaper', percent: 100 },
        '3836': { id: '3836', type: 'video', percent: 100 },
        '3834': { id: '3834', type: 'testpaper', percent: 100 },
      },
    };

    const passed = evaluateKuozhiProgress(
      g09,
      [complete],
      '2026-08-19T00:00:00.000Z',
    );
    const incomplete = evaluateKuozhiProgress(
      g09,
      [
        {
          ...complete,
          task_list: {
            ...complete.task_list,
            '3834': { id: '3834', type: 'testpaper', percent: 99 },
          },
        },
      ],
      '2026-08-19T00:00:00.000Z',
    );

    expect(passed.completion).toMatchObject({ completed: true });
    expect(incomplete.completion).toMatchObject({
      completed: false,
      reasonCode: 'REQUIREMENTS_INCOMPLETE',
    });
  });

  it('completes G03 only after its three videos and three assessments finish', () => {
    const g03: KuozhiResolvedMapping = {
      mappingVersion: 6,
      taskCode: 'G03',
      dataMode: 'REAL',
      queryTeacherId: 'TEACHER-001',
      mapping: {
        integrationStatus: 'ACTIVE',
        launchEnabled: true,
        completionEnabled: true,
        autoCompleteAssignment: true,
        noHeader: true,
        courses: [
          {
            courseId: '655',
            tasks: [
              {
                courseTaskId: '3781',
                type: 'VIDEO',
                required: true,
                completionPercent: 100,
              },
              { courseTaskId: '3783', type: 'TESTPAPER', required: true },
              {
                courseTaskId: '3784',
                type: 'VIDEO',
                required: true,
                completionPercent: 100,
              },
              { courseTaskId: '3785', type: 'TESTPAPER', required: true },
              {
                courseTaskId: '3786',
                type: 'VIDEO',
                required: true,
                completionPercent: 100,
              },
              { courseTaskId: '3787', type: 'TESTPAPER', required: true },
            ],
          },
        ],
      },
    };
    const complete = {
      id: '655',
      percent: 100,
      task_list: {
        '3781': { id: '3781', type: 'video', percent: 100 },
        '3783': { id: '3783', type: 'testpaper', percent: 100 },
        '3784': { id: '3784', type: 'video', percent: 100 },
        '3785': { id: '3785', type: 'testpaper', percent: 100 },
        '3786': { id: '3786', type: 'video', percent: 100 },
        '3787': { id: '3787', type: 'testpaper', percent: 100 },
      },
    };

    const passed = evaluateKuozhiProgress(
      g03,
      [complete],
      '2026-08-12T00:00:00.000Z',
    );
    const incomplete = evaluateKuozhiProgress(
      g03,
      [
        {
          ...complete,
          task_list: {
            ...complete.task_list,
            '3787': { id: '3787', type: 'testpaper', percent: 99 },
          },
        },
      ],
      '2026-08-12T00:00:00.000Z',
    );

    expect(passed.completion).toMatchObject({ completed: true });
    expect(incomplete.completion).toMatchObject({
      completed: false,
      reasonCode: 'REQUIREMENTS_INCOMPLETE',
    });
  });

  it('uses 100 percent progress to complete an exam', () => {
    const result = evaluateKuozhiProgress(
      resolved,
      [
        {
          id: '520',
          title: 'ME Culture and PARSNIP',
          percent: '100',
          task_list: {
            '2791': {
              id: '2791',
              title: 'Video',
              type: 'video',
              percent: 100,
            },
            '2792': {
              id: '2792',
              title: 'Exam',
              type: 'testpaper',
              percent: 100,
              score: '4.0',
              test_times: 1,
            },
          },
        },
      ],
      '2026-08-04T10:00:00.000Z',
    );

    expect(result.syncStatus).toBe('AVAILABLE');
    expect(result.completion).toEqual({
      enabled: true,
      completed: true,
      reasonCode: 'COMPLETED',
    });
    expect(result.courses[0].tasks[1]).toMatchObject({
      score: 4,
      percent: 100,
      completed: true,
    });
  });

  it('fails closed when a required source task is missing', () => {
    const result = evaluateKuozhiProgress(
      resolved,
      [
        {
          id: '520',
          title: 'ME Culture and PARSNIP',
          percent: '50',
          task_list: {
            '2791': { id: '2791', type: 'video', percent: 100 },
          },
        },
      ],
      '2026-08-04T10:00:00.000Z',
    );

    expect(result.syncStatus).toBe('PARTIAL');
    expect(result.completion.reasonCode).toBe('REQUIRED_TASK_MISSING');
    expect(result.completion.completed).toBe(false);
  });

  it('treats an empty course response as no data', () => {
    const result = evaluateKuozhiProgress(
      resolved,
      [{ id: null, title: null, task_list: null }],
      '2026-08-04T10:00:00.000Z',
    );

    expect(result.syncStatus).toBe('NO_DATA');
    expect(result.completion.reasonCode).toBe('NO_DATA');
  });

  it('keeps score only as evidence and does not use it as a pass line', () => {
    const result = evaluateKuozhiProgress(
      resolved,
      [
        {
          id: '520',
          title: 'ME Culture and PARSNIP',
          percent: 100,
          task_list: {
            '2791': { id: '2791', type: 'video', percent: 100 },
            '2792': {
              id: '2792',
              type: 'testpaper',
              percent: 99,
              score: 10,
              test_times: 1,
            },
          },
        },
      ],
      '2026-08-04T10:00:00.000Z',
    );

    expect(result.courses[0].tasks[1]).toMatchObject({
      score: 10,
      percent: 99,
      completed: false,
    });
    expect(result.completion.completed).toBe(false);
  });

  it('accepts an exam with percent 100 even when score is zero', () => {
    const result = evaluateKuozhiProgress(
      resolved,
      [
        {
          id: '520',
          title: 'ME Culture and PARSNIP',
          percent: 100,
          task_list: {
            '2791': { id: '2791', type: 'video', percent: 100 },
            '2792': {
              id: '2792',
              type: 'testpaper',
              percent: 100,
              score: 0,
              test_times: 0,
            },
          },
        },
      ],
      '2026-08-04T10:00:00.000Z',
    );

    expect(result.courses[0].tasks[1]).toMatchObject({
      score: 0,
      percent: 100,
      completed: true,
    });
  });
});
