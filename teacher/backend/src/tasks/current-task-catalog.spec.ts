import {
  currentTaskCatalog,
  isFullCatalogSync,
} from '../../scripts/sync-current-task-catalog';

interface ChecklistItem {
  key: string;
  label: string;
  labelZh: string;
}

describe('current task catalog locale fields', () => {
  it('does not run legacy retirements for a targeted G03 content publish', () => {
    expect(isFullCatalogSync(new Set(['G03']))).toBe(false);
    expect(isFullCatalogSync(new Set())).toBe(true);
    expect(
      isFullCatalogSync(new Set(currentTaskCatalog.map((task) => task.code))),
    ).toBe(true);
  });

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
        title: 'Lesson Preparation',
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
      G02: ['g02-policy-document'],
      G03: [],
      G04: ['g02-environment-photo', 'g02-courseware-confirmation'],
      G05: [],
      G06: [],
      G07: [],
      G08: [],
      G09: [],
    });
  });

  it('keeps G01 external completion limited to TESOL', () => {
    const g01 = currentTaskCatalog.find((task) => task.code === 'G01');
    const externalStatusRule = g01?.rules.find(
      (rule) => rule.type === 'G01_EXTERNAL_STATUS',
    );

    expect(g01).toMatchObject({
      whatToDo:
        'Confirm TESOL, pass the assessment in Kuozhi, complete the Essay and submit the completion proof.',
      completionStandard:
        'TESOL is complete, the Kuozhi assessment reaches 100% progress, the Essay is complete and the completion proof is submitted.',
    });
    expect(externalStatusRule).toMatchObject({
      version: '2026-08-11-tesol-only-v1',
      teacherFailureCopy: 'TESOL 真实状态尚未通过。',
    });
    expect(JSON.stringify(g01)).not.toMatch(/Self-intro|self_intro/i);
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

  it('publishes G03 as the mapped Kuozhi Student Types course', () => {
    const g03 = currentTaskCatalog.find((task) => task.code === 'G03');

    expect(g03).toMatchObject({
      contentStatus: 'READY',
      contentVersion: '2026-08-12-student-types-kuozhi-v1',
      steps: [],
      rules: [],
      whatToDo:
        'Complete the three student-type videos and pass each paired assessment in Kuozhi.',
      completionStandard:
        'All three videos reach 100% progress and all three paired assessments are passed in Kuozhi.',
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

  it('configures G04 as two independent photo and courseware parts', () => {
    const g04 = currentTaskCatalog.find((task) => task.code === 'G04');
    const guidanceStep = g04?.steps.find(
      (step) => step.key === 'g02-courseware-confirmation',
    );
    const completionRule = g04?.rules.find(
      (rule) => rule.type === 'ALL_STEPS_COMPLETE',
    );

    expect(g04?.independentModules).toEqual({
      stepKeys: ['g02-environment-photo', 'g02-courseware-confirmation'],
      allowOutOfOrderProgress: true,
      keepAssignmentInProgressUntilPassed: true,
    });
    expect(g04?.opsNameZh).toBe('首课准备');
    expect(g04?.steps).not.toEqual(
      expect.arrayContaining([
        expect.objectContaining({ key: 'g02-device-check' }),
      ]),
    );
    expect(guidanceStep?.config.version).toBe(
      'g02-courseware-2026-08-05-guidance-v1',
    );
    expect(completionRule?.version).toBe('2026-08-11-g04-two-part-v1');
    expect(completionRule?.config.requiredStepKeys).toEqual(
      g04?.independentModules?.stepKeys,
    );
  });

  it('pins the shared G04 How, completion standard and benefit copy', () => {
    const g04 = currentTaskCatalog.find((task) => task.code === 'G04');

    expect(g04).toMatchObject({
      title: 'Lesson Preparation',
      why: 'Complete the teaching-environment photo review and prepare the courseware before your first lesson.',
      whatToDo:
        'Complete two sections in any order: submit one teaching-environment photo for AI review and prepare the courseware for your first lesson. Each section keeps its own progress.',
      completionStandard:
        'G04 is completed only after both sections pass: all four teaching-environment photo criteria—camera angle, lighting, background and dressing—pass AI review, and the courseware preparation is confirmed. The sections may be completed in any order.',
      benefit:
        'Your teaching environment and courseware are ready for your first lesson.',
    });
  });

  it('keeps P-FB-NEGATIVE pending by default with one reviewed photo variant', () => {
    const task = currentTaskCatalog.find(
      (candidate) => candidate.code === 'P-FB-NEGATIVE',
    );

    expect(task).toMatchObject({
      contentStatus: 'PENDING',
      pendingReason: 'JIAHE_PERSONALIZED_CONTENT_PENDING',
      score: 0,
      contentVersion: '2026-08-11-personalized-environment-photo-v1',
      independentModules: {
        stepKeys: ['p-fb-negative-environment-photo'],
        allowOutOfOrderProgress: true,
        keepAssignmentInProgressUntilPassed: true,
      },
    });
    expect(task?.steps).toHaveLength(1);
    expect(task?.steps[0]).toMatchObject({
      key: 'p-fb-negative-environment-photo',
      type: 'UPLOAD',
      config: {
        role: 'ENVIRONMENT_PHOTO',
        reviewProfile: 'TEACHING_ENVIRONMENT_V1',
        captureOnly: true,
        accept: ['image/jpeg'],
      },
    });
    const imageRule = task?.rules.find(
      (candidate) => candidate.type === 'AI_IMAGE_REVIEW',
    );
    expect(imageRule?.config).toMatchObject({
      stepKey: 'p-fb-negative-environment-photo',
      reviewProfile: 'TEACHING_ENVIRONMENT_V1',
      criteriaKeys: ['camera_angle', 'lighting', 'background', 'dressing'],
    });
  });
});
