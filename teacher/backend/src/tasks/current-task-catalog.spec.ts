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
      G01: ['g01-essay-confirmation', 'g01-completion-proof'],
      G02: [],
      G03: [],
      G04: [
        'g02-device-check',
        'g02-courseware-confirmation',
        'g02-environment-photo',
      ],
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

  it('configures G04 as three independent parts with the stable device step', () => {
    const g04 = currentTaskCatalog.find((task) => task.code === 'G04');
    const deviceStep = g04?.steps.find(
      (step) => step.key === 'g02-device-check',
    );
    const guidanceStep = g04?.steps.find(
      (step) => step.key === 'g02-courseware-confirmation',
    );
    const completionRule = g04?.rules.find(
      (rule) => rule.type === 'ALL_STEPS_COMPLETE',
    );

    expect(g04?.independentModules).toEqual({
      stepKeys: [
        'g02-device-check',
        'g02-environment-photo',
        'g02-courseware-confirmation',
      ],
      allowOutOfOrderProgress: true,
      keepAssignmentInProgressUntilPassed: true,
    });
    expect(deviceStep).toMatchObject({
      type: 'DEVICE_CHECK',
      config: {
        version: 'g02-device-2026-08-05-browser-preflight-v1',
        role: 'DEVICE_CHECK',
        items: ['camera', 'microphone', 'network'],
      },
    });
    expect(guidanceStep?.config.version).toBe(
      'g02-courseware-2026-08-05-guidance-v1',
    );
    expect(completionRule?.version).toBe('2026-08-05-g04-three-part-v1');
    expect(completionRule?.config.requiredStepKeys).toEqual(
      g04?.independentModules?.stepKeys,
    );
  });

  it('pins the shared G04 How, completion standard and benefit copy', () => {
    const g04 = currentTaskCatalog.find((task) => task.code === 'G04');

    expect(g04).toMatchObject({
      whatToDo:
        'Complete three independent sections in any order: review the lesson-preparation guidance; run the camera, microphone and network check; and submit one teaching-environment photo for AI review. Each section keeps its own progress.',
      completionStandard:
        'G04 is completed only after all three independent sections pass: the lesson-preparation guidance is confirmed; the camera, microphone and network check passes; and all four teaching-environment photo criteria—camera angle, lighting, background and dressing—pass AI review. The sections may be completed in any order.',
      benefit:
        'Your lesson-preparation knowledge, device and network readiness, and teaching environment are independently verified for your first lesson.',
    });
  });
});
