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

  it('keeps the migrated G05 TTP step keys stable for saved progress', () => {
    const g05 = currentTaskCatalog.find((task) => task.code === 'G05');
    const checklist = g05?.steps.find(
      (step) => step.key === 'g06-learning-checklist',
    );
    const items = checklist?.config.items as ChecklistItem[];

    expect(items).toHaveLength(5);
    expect(items.map(({ key, labelZh }) => ({ key, labelZh }))).toEqual([
      {
        key: 'item-1',
        labelZh: 'TTP 与固定学员、开放时段和排期承诺的关系',
      },
      {
        key: 'item-2',
        labelZh: 'Shift Management 与 Auto Open Slot 的使用场景',
      },
      {
        key: 'item-3',
        labelZh: '开放时段的承诺周期与成熟日',
      },
      {
        key: 'item-4',
        labelZh: '已预约时段的履约要求和排期变化处理',
      },
      {
        key: 'item-5',
        labelZh: '持续维护 MyPage 可授课时段并了解支持入口',
      },
    ]);
  });

  it('keeps pre-renumber step identities attached to their actual semantics', () => {
    const stepKeysByTask = Object.fromEntries(
      currentTaskCatalog
        .filter((task) => task.kind === 'FIXED_GROWTH')
        .map((task) => [task.code, task.steps.map((step) => step.key)]),
    );

    expect(stepKeysByTask).toMatchObject({
      G01: ['g01-tesol-quiz', 'g01-essay-confirmation', 'g01-completion-proof'],
      G02: ['g03-platform-policies-document', 'g03-knowledge-check'],
      G03: [],
      G04: ['g02-courseware-confirmation', 'g02-environment-photo'],
      G05: ['g06-ttp-orientation-video', 'g06-learning-checklist'],
      G06: [
        'g07-me-culture-video',
        'g07-parsnip-thailand-1',
        'g07-parsnip-thailand-2',
        'g07-parsnip-saudi-1',
        'g07-parsnip-saudi-2',
        'g07-parsnip-malaysia',
        'g07-knowledge-check',
      ],
      G07: [
        'g08-attendance-policy',
        'g08-reliability-guide',
        'g08-knowledge-check',
      ],
      G08: [
        'g09-cocos-overview',
        'g09-cocos-alphabet',
        'g09-cocos-dialogue',
        'g09-cocos-phonics',
        'g09-cocos-reading',
        'g09-cocos-word-sentence',
        'g09-cocos-review-carnival',
        'g09-knowledge-check',
      ],
      G09: ['g10-set-fundamentals-video', 'g10-knowledge-check'],
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
