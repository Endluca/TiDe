import type { KuozhiResolvedMapping } from './kuozhi.models';
import { evaluateKuozhiProgress } from './kuozhi-progress.evaluator';

const resolved: KuozhiResolvedMapping = {
  mappingVersion: 3,
  taskCode: 'G06',
  dataMode: 'SAMPLE_DRY_RUN',
  queryTeacherId: '360107609',
  mapping: {
    integrationStatus: 'ACTIVE',
    launchEnabled: true,
    completionEnabled: true,
    noHeader: true,
    courses: [
      {
        courseId: '131',
        title: 'Sample',
        tasks: [
          {
            courseTaskId: '229',
            type: 'VIDEO',
            required: true,
            completionPercent: 100,
          },
          {
            courseTaskId: '231',
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
          id: '131',
          title: 'PSO Training OVS',
          percent: '100',
          task_list: {
            '229': { id: '229', title: 'Video', type: 'video', percent: 100 },
            '231': {
              id: '231',
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
          id: '131',
          title: 'PSO Training OVS',
          percent: '50',
          task_list: {
            '229': { id: '229', type: 'video', percent: 100 },
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
