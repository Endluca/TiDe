import { createHash } from 'node:crypto';
import { Client } from 'pg';

type StepType =
  | 'VIDEO'
  | 'DOCUMENT'
  | 'CHECKLIST'
  | 'UPLOAD'
  | 'DEVICE_CHECK'
  | 'EXTERNAL_TRAINING'
  | 'CUSTOM';

interface CatalogStep {
  key: string;
  type: StepType;
  title: string;
  config: Record<string, unknown>;
}

interface CatalogRule {
  key: string;
  type: string;
  version: string;
  config: Record<string, unknown>;
  teacherFailureCopy: string;
}

interface CatalogTask {
  code: string;
  externalCode?: string;
  opsNameZh?: string;
  title: string;
  why: string;
  whatToDo: string;
  completionStandard: string;
  benefit: string;
  priority: 'P0' | 'P1' | 'P2' | 'P3';
  score: number;
  stage: 'FOUNDATION' | 'INTEGRATION' | 'ADVANCE' | 'PERSONALIZED';
  sequence: number;
  estimatedMinutes: number;
  contentVersion?: string;
  contentStatus: 'READY' | 'PENDING';
  pendingReason?: string;
  independentModules?: {
    stepKeys: string[];
    allowOutOfOrderProgress: boolean;
    keepAssignmentInProgressUntilPassed: boolean;
  };
  allowRetry: boolean;
  kind: 'FIXED_GROWTH' | 'PERSONALIZED_IMPROVEMENT';
  steps: CatalogStep[];
  rules: CatalogRule[];
}

const uuidFor = (value: string): string => {
  const hash = createHash('sha256').update(value).digest('hex').slice(0, 32);
  return `${hash.slice(0, 8)}-${hash.slice(8, 12)}-4${hash.slice(13, 16)}-8${hash.slice(17, 20)}-${hash.slice(20)}`;
};

const allStepsRule = (
  copy: string,
  config: Record<string, unknown> = {},
  version = '2026-07-22',
): CatalogRule => ({
  key: 'all-steps-complete',
  type: 'ALL_STEPS_COMPLETE',
  version,
  config,
  teacherFailureCopy: copy,
});

const aiReviewRule = (input: {
  key: string;
  stepKey: string;
  criteriaVersion: string;
  criteriaKeys: string[];
  userText: string;
  allowedMimeTypes?: string[];
  reviewProfile?: string;
}): CatalogRule => ({
  key: input.key,
  type: 'AI_IMAGE_REVIEW',
  version: '2026-07-27-strict',
  config: {
    stepKey: input.stepKey,
    criteriaVersion: input.criteriaVersion,
    criteriaKeys: input.criteriaKeys,
    allowedMimeTypes: input.allowedMimeTypes ?? [
      'image/jpeg',
      'image/png',
      'image/webp',
    ],
    ...(input.reviewProfile
      ? { reviewProfile: input.reviewProfile }
      : {}),
    systemPrompt:
      'You strictly review teacher-submitted evidence. Return JSON only with this exact shape: {"decision":"PASS|RETRY|ERROR","teacherReason":"teacher-safe concise message","confidenceSummary":{},"criteria":[{"criterionKey":"one configured key","result":"PASS|FAIL|UNKNOWN","teacherMessage":"teacher-safe message or null"}]}. Include every configured criterion exactly once. Never infer a pass from the mere presence of a person or object. Use UNKNOWN whenever the visual evidence is unclear. PASS only when every configured criterion is visibly and unambiguously PASS; any FAIL or UNKNOWN requires RETRY. Use ERROR only when the file cannot be assessed. Do not expose internal risk labels or private model reasoning.',
    userText: input.userText,
  },
  teacherFailureCopy: '已保留你完成的内容，请根据提示更新这份材料。',
});

const environmentCriteria = [
  'camera_angle',
  'lighting',
  'background',
  'dressing',
];

const personalizedEnvironmentPhotoStepKey =
  'p-fb-negative-environment-photo';
const teachingEnvironmentReviewProfile = 'TEACHING_ENVIRONMENT_V1';
const personalizedEnvironmentContentVersion =
  '2026-08-11-personalized-environment-photo-v1';

const g04IndependentStepKeys = [
  'g02-environment-photo',
  'g02-courseware-confirmation',
];

const pending = (
  task: Omit<
    CatalogTask,
    'contentStatus' | 'pendingReason' | 'steps' | 'rules'
  >,
  reason: string,
): CatalogTask => ({
  ...task,
  contentStatus: 'PENDING',
  pendingReason: reason,
  steps: [],
  rules: [],
});

export const currentTaskCatalog: CatalogTask[] = [
  {
    code: 'G01',
    title: 'Profile & Credentials Completion',
    why: 'Complete the required TESOL status and learning evidence.',
    whatToDo:
      'Confirm TESOL, pass the assessment in Kuozhi, complete the Essay and submit the completion proof.',
    completionStandard:
      'TESOL is complete, the Kuozhi assessment reaches 100% progress, the Essay is complete and the completion proof is submitted.',
    benefit: 'Your profile and required TESOL learning evidence are complete.',
    priority: 'P1',
    score: 3,
    stage: 'FOUNDATION',
    sequence: 1,
    estimatedMinutes: 45,
    contentStatus: 'READY',
    allowRetry: true,
    kind: 'FIXED_GROWTH',
    steps: [
      {
        key: 'g01-essay-confirmation',
        type: 'CHECKLIST',
        title: 'Confirm the TESOL Essay is complete',
        config: {
          version: 'g01-essay-2026-07-23',
          role: 'TESOL_ESSAY',
          items: [
            {
              key: 'essay-completed',
              label: 'I have completed and submitted the required TESOL Essay.',
              labelZh: '我已完成并提交要求的 TESOL Essay。',
            },
          ],
        },
      },
      {
        key: 'g01-completion-proof',
        type: 'UPLOAD',
        title: 'Submit the TESOL completion proof',
        config: {
          version: 'g01-proof-2026-07-23',
          role: 'COMPLETION_PROOF',
          accept: ['image/jpeg', 'image/png', 'image/webp', 'application/pdf'],
          captureOnly: false,
          maxFiles: 1,
        },
      },
    ],
    rules: [
      allStepsRule('请确认 Essay，并提交完成证明。'),
      {
        key: 'g01-kuozhi-course',
        type: 'KUOZHI_COURSE_COMPLETE',
        version: '2026-08-06-percent-v1',
        config: { mappingVersion: 6 },
        teacherFailureCopy: '请先在阔知完成课程考试，并刷新学习进度。',
      },
      {
        key: 'g01-external-status',
        type: 'G01_EXTERNAL_STATUS',
        version: '2026-08-11-tesol-only-v1',
        config: {},
        teacherFailureCopy: 'TESOL 真实状态尚未通过。',
      },
    ],
  },
  {
    code: 'G04',
    opsNameZh: '首课准备',
    title: 'Lesson Preparation',
    why: 'Complete the teaching-environment photo review and prepare the courseware before your first lesson.',
    whatToDo:
      'Complete two sections in any order: submit one teaching-environment photo for AI review and prepare the courseware for your first lesson. Each section keeps its own progress.',
    completionStandard:
      'G04 is completed only after both sections pass: all four teaching-environment photo criteria—camera angle, lighting, background and dressing—pass AI review, and the courseware preparation is confirmed. The sections may be completed in any order.',
    benefit:
      'Your teaching environment and courseware are ready for your first lesson.',
    priority: 'P1',
    score: 3,
    stage: 'FOUNDATION',
    sequence: 4,
    estimatedMinutes: 15,
    contentVersion: '2026-08-11-g04-two-part',
    contentStatus: 'READY',
    independentModules: {
      stepKeys: g04IndependentStepKeys,
      allowOutOfOrderProgress: true,
      keepAssignmentInProgressUntilPassed: true,
    },
    allowRetry: true,
    kind: 'FIXED_GROWTH',
    steps: [
      {
        key: 'g02-environment-photo',
        type: 'UPLOAD',
        title: 'Take a teaching-environment photo',
        config: {
          version: 'g02-photo-2026-07-22',
          role: 'ENVIRONMENT_PHOTO',
          accept: ['image/jpeg'],
          captureOnly: true,
          maxFiles: 1,
        },
      },
      {
        key: 'g02-courseware-confirmation',
        type: 'CHECKLIST',
        title: 'Review and confirm lesson-preparation guidance',
        config: {
          version: 'g02-courseware-2026-08-05-guidance-v1',
          role: 'COURSEWARE_CONFIRMATION',
          items: [
            {
              key: 'courseware-prepared',
              label:
                'I have reviewed the lesson-preparation guidance and all slides, and I am ready for this lesson.',
              labelZh: '我已阅读备课须知并浏览全部课件，已完成本节课备课。',
            },
          ],
        },
      },
    ],
    rules: [
      allStepsRule(
        '请分别完成授课环境照片检查和课件准备确认，两部分可任意顺序完成。',
        { requiredStepKeys: g04IndependentStepKeys },
        '2026-08-11-g04-two-part-v1',
      ),
      aiReviewRule({
        key: 'g02-environment-ai-review',
        stepKey: 'g02-environment-photo',
        criteriaVersion:
          'lesson-preparation-camera-view-2026-08-v7-background-veto',
        criteriaKeys: environmentCriteria,
        userText:
          'Review this real teaching-environment photo strictly against camera angle, lighting, background and dressing only.',
      }),
    ],
  },
  {
    code: 'G02',
    title: 'Platform Policies',
    why: 'Learn the essential classroom and account-safety rules.',
    whatToDo:
      'Read the current Overseas NT Policies document in TIDE. Your reading progress is saved automatically.',
    completionStandard:
      'G02 is completed automatically after you reach the end of the current published document.',
    benefit: 'You can apply the core platform policies in class.',
    priority: 'P1',
    score: 2,
    stage: 'FOUNDATION',
    sequence: 2,
    estimatedMinutes: 35,
    contentVersion: '2026-07-24-overseas-nt-policies-v1',
    contentStatus: 'READY',
    allowRetry: true,
    kind: 'FIXED_GROWTH',
    steps: [
      {
        key: 'g02-policy-document',
        type: 'DOCUMENT',
        title: 'Read Overseas NT Policies',
        config: {
          role: 'POLICY_DOCUMENT',
          documentCode: 'overseas-nt-policies',
          sourceTitle: 'Overseas NT Policies',
          sourceNodeId: 'OG9lyrgJPzkq5xD6fvzmqRonWzN67Mw4',
          sourceUpdatedAt: '2026-07-24T01:47:08Z',
          contentVersion: '2026-07-24-overseas-nt-policies-v1',
          contentHash:
            '6875233667c6f3d90602a07c84849dbb88f41685929779a7b0dc79ccf859979c',
          readingCompletion: 'SCROLL_TO_END',
        },
      },
    ],
    rules: [
      allStepsRule(
        '请将当前版本的 Overseas NT Policies 阅读到文档末尾。',
        { requiredStepKeys: ['g02-policy-document'] },
        '2026-08-11-g02-policy-document-v1',
      ),
    ],
  },
  {
    code: 'G03',
    title: 'How to handle different types of students',
    why: 'Build practical responses for different learner needs.',
    whatToDo:
      'Complete the three student-type videos and pass each paired assessment in Kuozhi.',
    completionStandard:
      'All three videos reach 100% progress and all three paired assessments are passed in Kuozhi.',
    benefit: 'You can adapt your teaching to different learner types.',
    priority: 'P1',
    score: 2,
    stage: 'FOUNDATION',
    sequence: 3,
    estimatedMinutes: 15,
    contentVersion: '2026-08-12-student-types-kuozhi-v1',
    contentStatus: 'READY',
    allowRetry: true,
    kind: 'FIXED_GROWTH',
    steps: [],
    rules: [],
  },
  {
    code: 'G05',
    title: 'TTP Orientation',
    why: 'Understand TTP and its key business scenarios.',
    whatToDo: 'Complete the configured TTP course in Kuozhi.',
    completionStandard: 'All required Kuozhi video requirements pass.',
    benefit: 'You understand the key TTP workflow and commitments.',
    priority: 'P1',
    score: 3,
    stage: 'INTEGRATION',
    sequence: 5,
    estimatedMinutes: 10,
    contentStatus: 'READY',
    allowRetry: false,
    kind: 'FIXED_GROWTH',
    steps: [],
    rules: [],
  },
  {
    code: 'G06',
    title: 'ME Culture & PARSNIP',
    why: 'Learn cross-cultural classroom guidance.',
    whatToDo: 'Complete the configured courses and assessments in Kuozhi.',
    completionStandard:
      'All required Kuozhi videos and assessment requirements pass.',
    benefit: 'You can apply the culture guidance appropriately.',
    priority: 'P1',
    score: 4,
    stage: 'INTEGRATION',
    sequence: 6,
    estimatedMinutes: 38,
    contentStatus: 'READY',
    allowRetry: true,
    kind: 'FIXED_GROWTH',
    steps: [],
    rules: [],
  },
  {
    code: 'G07',
    title: 'Reliability Training',
    why: 'Strengthen dependable attendance habits.',
    whatToDo: 'Complete the configured Reliability course in Kuozhi.',
    completionStandard:
      'All published Kuozhi requirements pass; automatic completion stays disabled until the assessment mapping is available.',
    benefit: 'You have a clear reliability routine.',
    priority: 'P1',
    score: 3,
    stage: 'INTEGRATION',
    sequence: 7,
    estimatedMinutes: 15,
    contentStatus: 'READY',
    allowRetry: true,
    kind: 'FIXED_GROWTH',
    steps: [],
    rules: [],
  },
  {
    code: 'G08',
    title: 'Cocos Course Training',
    why: 'Learn the core Cocos teaching flow.',
    whatToDo: 'Complete the configured Cocos course and assessment in Kuozhi.',
    completionStandard:
      'All required Kuozhi videos and assessment requirements pass.',
    benefit: 'You can prepare for a Cocos class.',
    priority: 'P1',
    score: 5,
    stage: 'ADVANCE',
    sequence: 8,
    estimatedMinutes: 95,
    contentStatus: 'READY',
    allowRetry: true,
    kind: 'FIXED_GROWTH',
    steps: [],
    rules: [],
  },
  pending(
    {
      code: 'G09',
      title: 'SET Teaching Fundamentals',
      why: 'Learn the fundamentals of SET teaching.',
      whatToDo: 'Complete the SET course and assessment in Kuozhi.',
      completionStandard:
        'All required Kuozhi videos and assessment requirements pass.',
      benefit: 'You understand the SET teaching foundation.',
      priority: 'P1',
      score: 5,
      stage: 'ADVANCE',
      sequence: 9,
      estimatedMinutes: 25,
      allowRetry: true,
      kind: 'FIXED_GROWTH',
    },
    'KUOZHI_G09_COURSE_MAPPING_PENDING',
  ),
  {
    code: 'NT-Q03',
    externalCode: 'classroom-quality-reminder',
    title: 'Check Your Device and Connection',
    why: 'Recent classes show a device or connection improvement opportunity.',
    whatToDo: 'Complete the configured device and connection check.',
    completionStandard: 'Every configured check passes.',
    benefit: 'Your device and connection readiness is recorded.',
    priority: 'P2',
    score: 0,
    stage: 'PERSONALIZED',
    sequence: 1,
    estimatedMinutes: 4,
    contentStatus: 'READY',
    allowRetry: true,
    kind: 'PERSONALIZED_IMPROVEMENT',
    steps: [
      {
        key: 'device-check',
        type: 'DEVICE_CHECK',
        title: 'Check camera, microphone and network',
        config: {
          version: 'nt-q03-2026-07-22',
          role: 'DEVICE_CHECK',
          items: ['camera', 'microphone', 'network'],
        },
      },
    ],
    rules: [allStepsRule('请完成设备和连接检查。')],
  },
  ...[
    [
      'P-REL-ATTENDANCE',
      'attendance-reliability-refresher',
      'Be Ready and On Time',
      2,
    ],
    [
      'P-REL-MEMO',
      'lesson-memo-rules-learning',
      'Complete Your Lesson Memo',
      3,
    ],
    [
      'P-FB-COMPLAINT',
      'feedback-topic-learning',
      'Improve a Teaching Skill',
      5,
    ],
  ].map(([code, externalCode, title, sequence]) =>
    pending(
      {
        code: String(code),
        externalCode: String(externalCode),
        title: String(title),
        why: 'A teacher-safe improvement topic was assigned from confirmed business data.',
        whatToDo: 'Complete the learning content configured by Jiahe.',
        completionStandard:
          'Meet every requirement in the published task configuration.',
        benefit: 'You have completed the assigned improvement action.',
        priority: 'P2',
        score: 0,
        stage: 'PERSONALIZED',
        sequence: Number(sequence),
        estimatedMinutes: 8,
        allowRetry: true,
        kind: 'PERSONALIZED_IMPROVEMENT',
      },
      'JIAHE_PERSONALIZED_CONTENT_PENDING',
    ),
  ),
  {
    code: 'P-FB-NEGATIVE',
    externalCode: 'feedback-topic-learning',
    title: 'Feedback Improvement',
    why: 'The same negative-feedback signal has appeared more than once for this teacher.',
    whatToDo:
      'Complete the configured improvement activity for the feedback issue shown in the task reason. Depending on the assigned activity, you may need to submit a teaching-environment photo for review or complete another guided action.',
    completionStandard:
      'The teacher app marks the task as completed after every requirement for the assigned improvement activity, including any required photo review, is satisfied.',
    benefit:
      'This task carries no points. It targets a repeated learner-feedback issue.',
    priority: 'P2',
    score: 0,
    stage: 'PERSONALIZED',
    sequence: 4,
    estimatedMinutes: 8,
    contentVersion: personalizedEnvironmentContentVersion,
    contentStatus: 'PENDING',
    pendingReason: 'JIAHE_PERSONALIZED_CONTENT_PENDING',
    independentModules: {
      stepKeys: [personalizedEnvironmentPhotoStepKey],
      allowOutOfOrderProgress: true,
      keepAssignmentInProgressUntilPassed: true,
    },
    allowRetry: true,
    kind: 'PERSONALIZED_IMPROVEMENT',
    steps: [
      {
        key: personalizedEnvironmentPhotoStepKey,
        type: 'UPLOAD',
        title: 'Take a teaching-environment photo',
        config: {
          version: personalizedEnvironmentContentVersion,
          role: 'ENVIRONMENT_PHOTO',
          reviewProfile: teachingEnvironmentReviewProfile,
          accept: ['image/jpeg'],
          captureOnly: true,
          maxFiles: 1,
        },
      },
    ],
    rules: [
      allStepsRule(
        '请拍摄并提交一张当前授课环境照片。',
        { requiredStepKeys: [personalizedEnvironmentPhotoStepKey] },
        personalizedEnvironmentContentVersion,
      ),
      aiReviewRule({
        key: 'p-fb-negative-environment-ai-review',
        stepKey: personalizedEnvironmentPhotoStepKey,
        criteriaVersion: 'personalized-teaching-environment-2026-08-v1',
        criteriaKeys: environmentCriteria,
        reviewProfile: teachingEnvironmentReviewProfile,
        userText:
          'Review this current teaching-environment photo strictly against camera angle, lighting, background and dressing only.',
      }),
    ],
  },
  pending(
    {
      code: 'P-FB-BLACKLIST',
      externalCode: 'feedback-blacklist-review',
      title: 'Learner Experience Improvement',
      why: 'Operations confirmed that a teacher-facing improvement task should be assigned.',
      whatToDo: 'Complete the task content configured by Jiahe.',
      completionStandard:
        'Meet every requirement in the published task configuration.',
      benefit: 'You have completed the assigned improvement action.',
      priority: 'P2',
      score: 0,
      stage: 'PERSONALIZED',
      sequence: 6,
      estimatedMinutes: 8,
      allowRetry: true,
      kind: 'PERSONALIZED_IMPROVEMENT',
    },
    'JIAHE_PERSONALIZED_CONTENT_PENDING',
  ),
];

const currentFixedTasks = currentTaskCatalog.filter(
  (task) => task.kind === 'FIXED_GROWTH',
);

export const isFullCatalogSync = (requestedCodes: ReadonlySet<string>) =>
  requestedCodes.size === 0 ||
  currentTaskCatalog.every((task) => requestedCodes.has(task.code));

async function assertCurrentSharedCatalog(client: Client): Promise<void> {
  const result = await client.query<{
    taskCode: string;
    opsNameZh: string | null;
    title: string | null;
    score: string | null;
  }>(
    `
      SELECT
        template_id AS "taskCode",
        payload->>'ops_name_zh' AS "opsNameZh",
        payload->>'title' AS title,
        payload->>'score_value' AS score
      FROM public.task_templates
      WHERE status = 'PUBLISHED'
        AND template_id = ANY($1::varchar[])
      ORDER BY template_id
    `,
    [currentFixedTasks.map((task) => task.code)],
  );
  const actualByCode = new Map(result.rows.map((row) => [row.taskCode, row]));
  const mismatches = currentFixedTasks.flatMap((task) => {
    const actual = actualByCode.get(task.code);
    if (
      actual?.title === task.title &&
      Number(actual.score) === task.score &&
      (!task.opsNameZh || actual.opsNameZh === task.opsNameZh)
    ) {
      return [];
    }
    return [
      `${task.code}: expected ${task.opsNameZh ?? '-'}/${task.title}/${task.score}, got ` +
        `${actual?.opsNameZh ?? '-'}/${actual?.title ?? 'missing'}/${actual?.score ?? 'missing'}`,
    ];
  });
  if (mismatches.length > 0) {
    throw new Error(
      'Shared fixed-task catalog is not the current semantic G01-G09 ' +
        `contract. Apply the operations catalog migrations first: ${mismatches.join('; ')}`,
    );
  }
}

async function assertExecutionCatalogMigrated(client: Client): Promise<void> {
  const result = await client.query<{ mismatchCount: string }>(
    `
      SELECT count(*)::text AS "mismatchCount"
      FROM tide.task_execution_versions execution
      JOIN public.task_templates template
        ON template.row_id = execution.shared_template_row_id
      WHERE template.template_id = ANY($1::varchar[])
        AND execution.task_code IS DISTINCT FROM template.template_id
    `,
    [['G00', ...currentFixedTasks.map((task) => task.code)]],
  );
  if (Number(result.rows[0]?.mismatchCount ?? 0) > 0) {
    throw new Error(
      'Teacher execution codes still use the pre-G01-G09 catalog. ' +
        'Apply 0025_fixed_task_semantic_alignment before syncing content.',
    );
  }
}

const legacyPersonalizedTaskCodes = [
  'NT-R01',
  'NT-R03',
  'NT-F02-PACING',
  'NT-F02-INTERACTION',
  'NT-F02-CORRECTION',
  'NT-F03-SPEAKING-PACE',
  'NT-F03-SCAFFOLDING',
  'NT-F03-TEACHING-AIDS',
  'NT-F03-STUDENT-RESPONSE',
  'NT-F03-PRONUNCIATION',
  'NT-F04-PROFESSIONALISM',
  'NT-F05-BLACKLIST',
  'NT-F02-ATTITUDE',
  'NT-F03-LANGUAGE',
  'NT-F04-BOUNDARIES',
  'P-ENV-01',
];

async function retireLegacyPersonalizedCatalog(
  client: Client,
  allowPublicWrites: boolean,
): Promise<void> {
  await client.query(
    `
      UPDATE tide.task_execution_versions
      SET status = 'RETIRED', updated_at = now()
      WHERE task_code = ANY($1::varchar[])
        AND status <> 'RETIRED'
    `,
    [legacyPersonalizedTaskCodes],
  );
  if (!allowPublicWrites) return;
  await client.query(
    `
      UPDATE public.task_templates
      SET status = 'RETIRED', updated_by = 'tide_catalog_sync',
        updated_at = now()
      WHERE template_id = ANY($1::varchar[])
        AND status = 'PUBLISHED'
    `,
    [legacyPersonalizedTaskCodes],
  );
}

async function retireMergedSharedExecutions(client: Client): Promise<void> {
  await client.query(
    `
      UPDATE tide.task_execution_versions execution
      SET status = 'RETIRED', updated_at = now()
      FROM public.task_templates template
      WHERE execution.shared_template_row_id = template.row_id
        AND template.status = 'RETIRED'
        AND template.template_id = 'G00'
        AND execution.status <> 'RETIRED'
    `,
  );
}

async function upsertTemplate(
  client: Client,
  task: CatalogTask,
  allowPublicWrites: boolean,
): Promise<string> {
  if (!allowPublicWrites) {
    const existingExecution = await client.query<{
      rowId: string;
      status: string;
    }>(
      `
        SELECT template.row_id AS "rowId", template.status
        FROM tide.task_execution_versions execution
        JOIN public.task_templates template
          ON template.row_id = execution.shared_template_row_id
        WHERE execution.task_code = $1
          AND template.template_id = $1
        LIMIT 1
      `,
      [task.code],
    );
    if (existingExecution.rows[0]?.status === 'PUBLISHED') {
      return existingExecution.rows[0].rowId;
    }
  }
  const existing = await client.query<{ rowId: string; status: string }>(
    `
      SELECT row_id AS "rowId", status
      FROM public.task_templates
      WHERE template_id = $1
      ORDER BY (status = 'PUBLISHED') DESC, template_version DESC
      LIMIT 1
    `,
    [task.code],
  );
  const rowId = existing.rows[0]?.rowId ?? `${task.code}:v1`;
  if (!allowPublicWrites) {
    if (!existing.rows[0] || existing.rows[0].status !== 'PUBLISHED') {
      throw new Error(
        `Published shared template ${task.code} is required in read-only mode.`,
      );
    }
    return rowId;
  }
  const payload = {
    template_id: task.code,
    ...(task.opsNameZh ? { ops_name_zh: task.opsNameZh } : {}),
    title: task.title,
    why_template: task.why,
    how_summary: task.whatToDo,
    completion_standard: task.completionStandard,
    benefit: task.benefit,
    category:
      task.kind === 'FIXED_GROWTH'
        ? 'MANDATORY_GROWTH'
        : 'PERSONALIZED_IMPROVEMENT',
    content_status: task.contentStatus === 'READY' ? 'READY' : 'PENDING_JIAHE',
    priority: task.priority,
    score_value: task.score,
    stage: task.stage,
    sequence: task.sequence,
    content_locale: 'en',
  };
  if (existing.rows[0]) {
    await client.query(
      `
        UPDATE public.task_templates
        SET status = 'PUBLISHED', payload = $2, external_task_template_code = $3,
          integration_mode = $4, updated_by = 'tide_catalog_sync',
          updated_at = now()
        WHERE row_id = $1
      `,
      [
        rowId,
        payload,
        task.kind === 'FIXED_GROWTH'
          ? `TIT.${task.code}`
          : (task.externalCode ?? task.code),
        task.kind === 'FIXED_GROWTH'
          ? 'INBOUND_STATUS_ONLY'
          : 'OUTBOUND_MANAGED',
      ],
    );
    return rowId;
  }
  await client.query(
    `
      INSERT INTO public.task_templates (
        row_id, template_id, template_version, status, revision,
        output_type, execution_owner, external_task_template_code,
        source_mode, payload, created_by, updated_by, integration_mode
      ) VALUES (
        $1, $2, 1, 'PUBLISHED', 1, 'TEACHER_TASK', 'TEACHER_APP',
        $3, 'REAL', $4, 'tide_catalog_sync', 'tide_catalog_sync', $5
      )
    `,
    [
      rowId,
      task.code,
      task.kind === 'FIXED_GROWTH'
        ? `TIT.${task.code}`
        : (task.externalCode ?? task.code),
      payload,
      task.kind === 'FIXED_GROWTH' ? 'INBOUND_STATUS_ONLY' : 'OUTBOUND_MANAGED',
    ],
  );
  return rowId;
}

async function syncTask(
  client: Client,
  task: CatalogTask,
  allowPublicWrites: boolean,
): Promise<void> {
  const templateRowId = await upsertTemplate(client, task, allowPublicWrites);
  const executionId = uuidFor(`execution:${templateRowId}`);
  await client.query(
    `
      INSERT INTO tide.task_execution_versions (
        id, shared_template_row_id, task_code, execution_contract_version,
        config, status
      ) VALUES ($1, $2, $3, 'task-contract-v3', $4, 'ACTIVE')
      ON CONFLICT (shared_template_row_id) DO UPDATE SET
        task_code = EXCLUDED.task_code,
        execution_contract_version = EXCLUDED.execution_contract_version,
        config = EXCLUDED.config,
        status = 'ACTIVE',
        updated_at = now()
      WHERE (
        tide.task_execution_versions.task_code,
        tide.task_execution_versions.execution_contract_version,
        tide.task_execution_versions.config,
        tide.task_execution_versions.status
      ) IS DISTINCT FROM (
        EXCLUDED.task_code,
        EXCLUDED.execution_contract_version,
        EXCLUDED.config,
        EXCLUDED.status
      )
    `,
    [
      executionId,
      templateRowId,
      task.code,
      {
        estimatedMinutes: task.estimatedMinutes,
        allowRetry: task.allowRetry,
        contentStatus: task.contentStatus,
        contentVersion: task.contentVersion ?? '2026-08-06',
        pendingReason: task.pendingReason ?? null,
        ...(task.independentModules
          ? { independentModules: task.independentModules }
          : {}),
      },
    ],
  );
  const actualExecution = await client.query<{ id: string }>(
    'SELECT id FROM tide.task_execution_versions WHERE shared_template_row_id = $1',
    [templateRowId],
  );
  const id = actualExecution.rows[0].id;
  await client.query(
    `
      UPDATE tide.task_step_definitions
      SET position = position + 1000000
      WHERE execution_version_id = $1
    `,
    [id],
  );
  await client.query(
    `
      UPDATE tide.task_validation_rules
      SET position = position + 1000000
      WHERE execution_version_id = $1
    `,
    [id],
  );
  for (const [index, step] of task.steps.entries()) {
    await client.query(
      `
        INSERT INTO tide.task_step_definitions (
          id, execution_version_id, step_key, position, step_type, title, config
        ) VALUES ($1, $2, $3, $4, $5, $6, $7)
        ON CONFLICT (execution_version_id, step_key) DO UPDATE SET
          position = EXCLUDED.position,
          step_type = EXCLUDED.step_type,
          title = EXCLUDED.title,
          config = EXCLUDED.config
      `,
      [
        uuidFor(`step:${task.code}:${step.key}`),
        id,
        step.key,
        index + 1,
        step.type,
        step.title,
        step.config,
      ],
    );
  }
  await client.query(
    `
      DELETE FROM tide.task_step_definitions
      WHERE execution_version_id = $1
        AND NOT (step_key = ANY($2::text[]))
    `,
    [id, task.steps.map((step) => step.key)],
  );
  for (const [index, rule] of task.rules.entries()) {
    await client.query(
      `
        INSERT INTO tide.task_validation_rules (
          id, execution_version_id, rule_key, rule_type, rule_version,
          position, config, teacher_failure_copy
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        ON CONFLICT (execution_version_id, rule_key) DO UPDATE SET
          rule_type = EXCLUDED.rule_type,
          rule_version = EXCLUDED.rule_version,
          position = EXCLUDED.position,
          config = EXCLUDED.config,
          teacher_failure_copy = EXCLUDED.teacher_failure_copy
      `,
      [
        uuidFor(`rule:${task.code}:${rule.key}`),
        id,
        rule.key,
        rule.type,
        rule.version,
        index + 1,
        rule.config,
        rule.teacherFailureCopy,
      ],
    );
  }
  await client.query(
    `
      DELETE FROM tide.task_validation_rules
      WHERE execution_version_id = $1
        AND NOT (rule_key = ANY($2::text[]))
    `,
    [id, task.rules.map((rule) => rule.key)],
  );
}

async function main(): Promise<void> {
  const requestedCodes = new Set(
    (process.env.TASK_CATALOG_TASK_CODES ?? '')
      .split(',')
      .map((code) => code.trim())
      .filter(Boolean),
  );
  const selectedTasks =
    requestedCodes.size === 0
      ? currentTaskCatalog
      : currentTaskCatalog.filter((task) => requestedCodes.has(task.code));
  const missingCodes = [...requestedCodes].filter(
    (code) => !selectedTasks.some((task) => task.code === code),
  );
  if (missingCodes.length > 0) {
    throw new Error(
      `Unknown task catalog codes: ${missingCodes.sort().join(', ')}`,
    );
  }
  const allowPublicWrites =
    process.env.TASK_CATALOG_PUBLIC_WRITE?.toLowerCase() === 'true';
  const shouldRetireLegacyCatalog = isFullCatalogSync(requestedCodes);
  const client = new Client(
    process.env.TASK_CATALOG_DATABASE_URL
      ? { connectionString: process.env.TASK_CATALOG_DATABASE_URL }
      : {
          host: process.env.TIDE_DB_HOST ?? '127.0.0.1',
          port: Number(process.env.TIDE_DB_PORT ?? 55432),
          user: process.env.TIDE_DB_USER,
          password: process.env.TIDE_DB_PASSWORD,
          database: process.env.TIDE_DB_NAME,
        },
  );
  await client.connect();
  try {
    await client.query('BEGIN');
    await assertCurrentSharedCatalog(client);
    await assertExecutionCatalogMigrated(client);
    if (shouldRetireLegacyCatalog) {
      await retireLegacyPersonalizedCatalog(client, allowPublicWrites);
      await retireMergedSharedExecutions(client);
    }
    for (const task of selectedTasks) {
      await syncTask(client, task, allowPublicWrites);
    }
    await client.query('COMMIT');
    process.stdout.write(
      `Synced ${selectedTasks.length} current task execution configs` +
        ` (shared template writes: ${allowPublicWrites ? 'enabled' : 'disabled'}).\n`,
    );
  } catch (error) {
    await client.query('ROLLBACK');
    throw error;
  } finally {
    await client.end();
  }
}

if (require.main === module) {
  void main();
}
