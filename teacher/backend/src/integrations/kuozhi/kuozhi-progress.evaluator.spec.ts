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
