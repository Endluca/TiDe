import type { KuozhiResolvedMapping } from './kuozhi.models';
import { evaluateKuozhiProgress } from './kuozhi-progress.evaluator';

const resolved: KuozhiResolvedMapping = {
  mappingVersion: 4,
  taskCode: 'G06',
  dataMode: 'REAL',
  queryTeacherId: 'TEACHER-001',
  mapping: {
    integrationStatus: 'ACTIVE',
    launchEnabled: true,
    completionEnabled: true,
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
            scoreMode: 'RAW_POINTS',
            fullScore: 5,
            passScore: { kind: 'FIXED', percent: 80 },
          },
        ],
      },
    ],
  },
};

describe('evaluateKuozhiProgress', () => {
  it('normalizes raw exam points and completes all required tasks', () => {
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
      new Map(),
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
      normalizedScorePercent: 80,
      passScorePercent: 80,
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
      new Map(),
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
      new Map(),
      '2026-08-04T10:00:00.000Z',
    );

    expect(result.syncStatus).toBe('NO_DATA');
    expect(result.completion.reasonCode).toBe('NO_DATA');
  });
});
