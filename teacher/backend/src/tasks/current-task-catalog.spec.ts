import { currentTaskCatalog } from '../../scripts/sync-current-task-catalog';

interface ChecklistItem {
  key: string;
  label: string;
  labelZh: string;
}

describe('current task catalog locale fields', () => {
  it('matches the operations-side G01-G09 semantics and score contract', () => {
    expect(
      currentTaskCatalog
        .filter((task) => task.kind === 'FIXED_GROWTH')
        .sort((left, right) => left.code.localeCompare(right.code))
        .map(({ code, title, score, stage, sequence }) => ({
          code,
          title,
          score,
          stage,
          sequence,
        })),
    ).toEqual([
      {
        code: 'G01',
        title: 'Profile & Credentials Completion',
        score: 3,
        stage: 'FOUNDATION',
        sequence: 1,
      },
      {
        code: 'G02',
        title: 'Platform Policies',
        score: 2,
        stage: 'FOUNDATION',
        sequence: 2,
      },
      {
        code: 'G03',
        title: 'How to handle different types of students',
        score: 2,
        stage: 'FOUNDATION',
        sequence: 3,
      },
      {
        code: 'G04',
        title: 'Lesson Preparation&Device Network Check',
        score: 3,
        stage: 'FOUNDATION',
        sequence: 4,
      },
      {
        code: 'G05',
        title: 'TTP Orientation',
        score: 3,
        stage: 'INTEGRATION',
        sequence: 5,
      },
      {
        code: 'G06',
        title: 'ME Culture & PARSNIP',
        score: 4,
        stage: 'INTEGRATION',
        sequence: 6,
      },
      {
        code: 'G07',
        title: 'Reliability Training',
        score: 3,
        stage: 'INTEGRATION',
        sequence: 7,
      },
      {
        code: 'G08',
        title: 'Cocos Course Training',
        score: 5,
        stage: 'ADVANCE',
        sequence: 8,
      },
      {
        code: 'G09',
        title: 'SET Teaching Fundamentals',
        score: 5,
        stage: 'ADVANCE',
        sequence: 9,
      },
    ]);
  });

  it('keeps every checklist item in English with a Chinese locale value', () => {
    const checklistItems = currentTaskCatalog
      .flatMap((task) => task.steps)
      .filter((step) => step.type === 'CHECKLIST')
      .flatMap((step) => step.config.items as ChecklistItem[]);

    expect(checklistItems).not.toHaveLength(0);
    for (const item of checklistItems) {
      expect(item.key).toEqual(expect.any(String));
      expect(item.label).toEqual(expect.any(String));
      expect(item.label).not.toMatch(/\p{Script=Han}/u);
      expect(item.labelZh).toMatch(/\p{Script=Han}/u);
    }
  });

  it('keeps local steps only for tasks that still execute inside TIDE', () => {
    const stepKeysByTask = Object.fromEntries(
      currentTaskCatalog
        .filter((task) => task.kind === 'FIXED_GROWTH')
        .map((task) => [task.code, task.steps.map((step) => step.key)]),
    );

    expect(stepKeysByTask).toMatchObject({
      G01: ['g01-tesol-quiz', 'g01-essay-confirmation', 'g01-completion-proof'],
      G02: [],
      G03: [],
      G04: ['g02-courseware-confirmation', 'g02-environment-photo'],
      G05: [],
      G06: [],
      G07: [],
      G08: [],
      G09: [],
    });
  });

  it('keeps G09 pending until Kuozhi publishes its course mapping', () => {
    const g09 = currentTaskCatalog.find((task) => task.code === 'G09');

    expect(g09).toMatchObject({
      contentStatus: 'PENDING',
      pendingReason: 'KUOZHI_G09_COURSE_MAPPING_PENDING',
      steps: [],
      rules: [],
    });
  });

  it("keeps the current G04 image review limited to Sophia's four checks", () => {
    const g04 = currentTaskCatalog.find((task) => task.code === 'G04');
    const imageReview = g04?.rules.find(
      (rule) => rule.type === 'AI_IMAGE_REVIEW',
    );

    expect(imageReview?.config.criteriaKeys).toEqual([
      'camera_angle',
      'lighting',
      'background',
      'dressing',
    ]);
    expect(imageReview?.config.criteriaVersion).toBe(
      'lesson-preparation-camera-view-2026-08-v7-background-veto',
    );
  });
});
