import { createHash } from 'node:crypto';
import { Client } from 'pg';

type StepType =
  | 'VIDEO'
  | 'DOCUMENT'
  | 'QUIZ'
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
  contentStatus: 'READY' | 'PENDING';
  pendingReason?: string;
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
): CatalogRule => ({
  key: 'all-steps-complete',
  type: 'ALL_STEPS_COMPLETE',
  version: '2026-07-22',
  config,
  teacherFailureCopy: copy,
});

const videoStep = (input: {
  key: string;
  title: string;
  titleZh: string;
  assetUrl: string;
  durationSeconds: number;
  mediaVersion: string;
  mock?: boolean;
}): CatalogStep => ({
  key: input.key,
  type: 'VIDEO',
  title: input.title,
  config: {
    titleZh: input.titleZh,
    assetUrl: input.assetUrl,
    durationSeconds: input.durationSeconds,
    mediaVersion: input.mediaVersion,
    chapterId: input.key,
    mock: input.mock ?? true,
  },
});

const documentStep = (input: {
  key: string;
  title: string;
  configVersion: string;
  sourceTitle: string;
  sourceUpdatedAt: string;
}): CatalogStep => ({
  key: input.key,
  type: 'DOCUMENT',
  title: input.title,
  config: {
    version: input.configVersion,
    role: 'REQUIRED_READING',
    sourceTitle: input.sourceTitle,
    sourceUpdatedAt: input.sourceUpdatedAt,
  },
});

const publishedQuizStep = (input: {
  key: string;
  title: string;
  configVersion: string;
  quizBankKey: string;
  questionSetVersion: string;
  expectedQuestionCount: number;
  mock?: boolean;
}): CatalogStep => ({
  key: input.key,
  type: 'QUIZ',
  title: input.title,
  config: {
    version: input.configVersion,
    role: 'KNOWLEDGE_CHECK',
    quizBankKey: input.quizBankKey,
    questionSetVersion: input.questionSetVersion,
    expectedQuestionCount: input.expectedQuestionCount,
    mock: input.mock ?? true,
  },
});

const aiReviewRule = (input: {
  key: string;
  stepKey: string;
  criteriaVersion: string;
  criteriaKeys: string[];
  userText: string;
  allowedMimeTypes?: string[];
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
    why: 'Complete the required profile statuses and TESOL learning evidence.',
    whatToDo:
      'Confirm Self-intro and TESOL, pass all 61 questions, complete the Essay and submit the completion proof.',
    completionStandard:
      'Self-intro and TESOL are complete, the 61-question check reaches 80%, the Essay is complete and the completion proof is submitted.',
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
        key: 'g01-tesol-quiz',
        type: 'QUIZ',
        title: 'Complete the 61-question TESOL check',
        config: {
          version: 'g01-tesol-quiz-2026-07-23',
          role: 'TESOL_QUIZ',
          quizBankKey: 'profile-credentials',
          questionSetVersion: 'course-407-2026-07-v2',
          expectedQuestionCount: 61,
          mock: true,
        },
      },
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
      allStepsRule('请完成 61 题测验、Essay，并提交完成证明。'),
      {
        key: 'g01-external-status',
        type: 'G01_EXTERNAL_STATUS',
        version: '2026-07-22',
        config: {},
        teacherFailureCopy: 'Self-intro 和 TESOL 真实状态尚未全部通过。',
      },
    ],
  },
  {
    code: 'G04',
    title: 'Lesson Preparation&Device Network Check',
    why: 'Complete lesson preparation and confirm that your teaching setup is ready before class.',
    whatToDo:
      'Take one teaching-environment photo for AI review, then confirm that lesson preparation is complete.',
    completionStandard:
      'The teaching-environment photo passes AI review and lesson preparation is confirmed.',
    benefit:
      'Your lesson preparation and pre-class teaching view are recorded as ready.',
    priority: 'P1',
    score: 3,
    stage: 'FOUNDATION',
    sequence: 4,
    estimatedMinutes: 15,
    contentStatus: 'READY',
    allowRetry: true,
    kind: 'FIXED_GROWTH',
    steps: [
      {
        key: 'g02-courseware-confirmation',
        type: 'CHECKLIST',
        title: 'Confirm courseware preparation',
        config: {
          version: 'g02-courseware-2026-07-28',
          role: 'COURSEWARE_CONFIRMATION',
          items: [
            {
              key: 'courseware-prepared',
              label:
                'I have reviewed all the slides and finished preparing for this lesson.',
              labelZh: '我已浏览全部课件，并完成本节课备课。',
            },
          ],
        },
      },
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
    ],
    rules: [
      allStepsRule('请完成备课确认和授课环境照片检查。'),
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
    whatToDo: 'Read the in-platform policy guide and complete its quiz.',
    completionStandard:
      'The policy guide is confirmed and the quiz requirements pass.',
    benefit: 'You can apply the core platform policies in class.',
    priority: 'P1',
    score: 2,
    stage: 'FOUNDATION',
    sequence: 2,
    estimatedMinutes: 15,
    contentStatus: 'READY',
    allowRetry: true,
    kind: 'FIXED_GROWTH',
    steps: [
      documentStep({
        key: 'g03-platform-policies-document',
        title: 'Read the Platform Policies guide',
        configVersion: 'g03-platform-policies-document-2026-07',
        sourceTitle: 'Overseas NT Policies',
        sourceUpdatedAt: '2026-07',
      }),
      publishedQuizStep({
        key: 'g03-knowledge-check',
        title: 'Complete the Platform Policies knowledge check',
        configVersion: 'g03-quiz-2026-07-23',
        quizBankKey: 'platform-policies',
        questionSetVersion: 'course-499-2026-07',
        expectedQuestionCount: 5,
      }),
    ],
    rules: [allStepsRule('请先完成平台规则文档阅读并通过知识检查。')],
  },
  pending(
    {
      code: 'G03',
      title: 'How to handle different types of students',
      why: 'Build practical responses for different learner needs.',
      whatToDo: 'Complete the learning content configured by Jiahe.',
      completionStandard:
        'Meet every requirement in the published Student Types configuration.',
      benefit: 'You can adapt your teaching to different learner types.',
      priority: 'P1',
      score: 2,
      stage: 'FOUNDATION',
      sequence: 3,
      estimatedMinutes: 15,
      allowRetry: true,
      kind: 'FIXED_GROWTH',
    },
    'JIAHE_STUDENT_TYPES_CONFIG_PENDING',
  ),
  {
    code: 'G05',
    title: 'TTP Orientation',
    why: 'Understand TTP and its key business scenarios.',
    whatToDo:
      'Watch the in-platform TTP video and confirm every item in the learning checklist.',
    completionStandard:
      'The TTP video is watched in full and every published checklist item is confirmed.',
    benefit: 'You understand the key TTP workflow and commitments.',
    priority: 'P1',
    score: 3,
    stage: 'INTEGRATION',
    sequence: 5,
    estimatedMinutes: 10,
    contentStatus: 'READY',
    allowRetry: false,
    kind: 'FIXED_GROWTH',
    steps: [
      videoStep({
        key: 'g06-ttp-orientation-video',
        title: 'Teacher Tie-up Program',
        titleZh: 'TTP 入门培训',
        assetUrl: '/videos/g06-ttp-orientation/v2/01.mp4',
        durationSeconds: 314,
        mediaVersion: 'g06-ttp-orientation-v2-01',
      }),
      {
        key: 'g06-learning-checklist',
        type: 'CHECKLIST',
        title: 'Complete the TTP learning checklist',
        config: {
          version: 'g06-checklist-2026-07-23',
          role: 'LEARNING_CHECKLIST',
          referenceVideo: {
            title: 'TTP Orientation',
            titleZh: 'TTP 入门培训',
            assetUrl: '/videos/g06-ttp-orientation/v2/01.mp4',
            durationSeconds: 314,
            mediaVersion: 'g06-ttp-orientation-v2-01',
          },
          items: [
            {
              key: 'item-1',
              label:
                'How TTP connects fixed students, open slots, and scheduling commitments.',
              labelZh: 'TTP 与固定学员、开放时段和排期承诺的关系',
            },
            {
              key: 'item-2',
              label: 'When to use Shift Management and Auto Open Slot.',
              labelZh: 'Shift Management 与 Auto Open Slot 的使用场景',
            },
            {
              key: 'item-3',
              label: 'The commitment period and maturity date for open slots.',
              labelZh: '开放时段的承诺周期与成熟日',
            },
            {
              key: 'item-4',
              label:
                'How to fulfill booked slots and handle scheduling changes.',
              labelZh: '已预约时段的履约要求和排期变化处理',
            },
            {
              key: 'item-5',
              label:
                'Keep MyPage availability up to date and know where to get support.',
              labelZh: '持续维护 MyPage 可授课时段并了解支持入口',
            },
          ],
        },
      },
    ],
    rules: [allStepsRule('请先完整观看 TTP 视频，并确认全部学习清单。')],
  },
  {
    code: 'G06',
    title: 'ME Culture & PARSNIP',
    why: 'Learn cross-cultural classroom guidance.',
    whatToDo: 'Complete the configured videos and quiz.',
    completionStandard: 'All configured videos and quiz requirements pass.',
    benefit: 'You can apply the culture guidance appropriately.',
    priority: 'P1',
    score: 4,
    stage: 'INTEGRATION',
    sequence: 6,
    estimatedMinutes: 38,
    contentStatus: 'READY',
    allowRetry: true,
    kind: 'FIXED_GROWTH',
    steps: [
      videoStep({
        key: 'g07-me-culture-video',
        title: 'ME Culture Training',
        titleZh: 'ME 文化培训',
        assetUrl: '/videos/g07-me-culture/v2/01.mp4',
        durationSeconds: 255,
        mediaVersion: 'g07-me-culture-v2-01',
      }),
      videoStep({
        key: 'g07-parsnip-thailand-1',
        title: 'Global PARSNIP · Thailand Part 1',
        titleZh: 'Global PARSNIP · 泰国（一）',
        assetUrl: '/videos/g07-me-culture/v2/02.mp4',
        durationSeconds: 404,
        mediaVersion: 'g07-me-culture-v2-02',
      }),
      videoStep({
        key: 'g07-parsnip-thailand-2',
        title: 'Global PARSNIP · Thailand Part 2',
        titleZh: 'Global PARSNIP · 泰国（二）',
        assetUrl: '/videos/g07-me-culture/v2/03.mp4',
        durationSeconds: 291,
        mediaVersion: 'g07-me-culture-v2-03',
      }),
      videoStep({
        key: 'g07-parsnip-saudi-1',
        title: 'Global PARSNIP · Saudi Arabia Part 1',
        titleZh: 'Global PARSNIP · 沙特阿拉伯（一）',
        assetUrl: '/videos/g07-me-culture/v2/04.mp4',
        durationSeconds: 547,
        mediaVersion: 'g07-me-culture-v2-04',
      }),
      videoStep({
        key: 'g07-parsnip-saudi-2',
        title: 'Global PARSNIP · Saudi Arabia Part 2',
        titleZh: 'Global PARSNIP · 沙特阿拉伯（二）',
        assetUrl: '/videos/g07-me-culture/v2/05.mp4',
        durationSeconds: 129,
        mediaVersion: 'g07-me-culture-v2-05',
      }),
      videoStep({
        key: 'g07-parsnip-malaysia',
        title: 'Global PARSNIP · Malaysia',
        titleZh: 'Global PARSNIP · 马来西亚',
        assetUrl: '/videos/g07-me-culture/v2/06.mp4',
        durationSeconds: 596,
        mediaVersion: 'g07-me-culture-v2-06',
      }),
      publishedQuizStep({
        key: 'g07-knowledge-check',
        title: 'Complete the ME Culture and PARSNIP knowledge check',
        configVersion: 'g07-quiz-2026-07-23',
        quizBankKey: 'me-culture',
        questionSetVersion: 'courses-520-398-2026-07',
        expectedQuestionCount: 30,
      }),
    ],
    rules: [allStepsRule('请先按顺序完整观看全部视频并完成知识检查。')],
  },
  {
    code: 'G07',
    title: 'Reliability Training',
    why: 'Strengthen dependable attendance habits.',
    whatToDo: 'Complete the configured training and quiz.',
    completionStandard: 'All configured training and quiz requirements pass.',
    benefit: 'You have a clear reliability routine.',
    priority: 'P1',
    score: 3,
    stage: 'INTEGRATION',
    sequence: 7,
    estimatedMinutes: 15,
    contentStatus: 'READY',
    allowRetry: true,
    kind: 'FIXED_GROWTH',
    steps: [
      videoStep({
        key: 'g08-attendance-policy',
        title: 'Attendance Policy Primer',
        titleZh: '出勤政策入门',
        assetUrl: '/videos/g08-reliability-training/v2/01.mp4',
        durationSeconds: 357,
        mediaVersion: 'g08-reliability-training-v2-01',
      }),
      videoStep({
        key: 'g08-reliability-guide',
        title: 'Attendance Reliability Guide',
        titleZh: '出勤可靠性指南',
        assetUrl: '/videos/g08-reliability-training/v2/02.mp4',
        durationSeconds: 314,
        mediaVersion: 'g08-reliability-training-v2-02',
      }),
      publishedQuizStep({
        key: 'g08-knowledge-check',
        title: 'Complete the Reliability knowledge check',
        configVersion: 'g08-quiz-mock-2026-07-24',
        quizBankKey: 'reliability-training',
        questionSetVersion: 'mock-course-595-2026-07-v1',
        expectedQuestionCount: 5,
      }),
    ],
    rules: [allStepsRule('请先按顺序完整观看全部视频并完成知识检查。')],
  },
  {
    code: 'G08',
    title: 'Cocos Course Training',
    why: 'Learn the core Cocos teaching flow.',
    whatToDo: 'Complete the configured in-platform videos and quiz.',
    completionStandard: 'All configured videos and quiz requirements pass.',
    benefit: 'You can prepare for a Cocos class.',
    priority: 'P1',
    score: 5,
    stage: 'ADVANCE',
    sequence: 8,
    estimatedMinutes: 95,
    contentStatus: 'READY',
    allowRetry: true,
    kind: 'FIXED_GROWTH',
    steps: [
      videoStep({
        key: 'g09-cocos-overview',
        title: 'Course Overview',
        titleZh: '课程概览',
        assetUrl: '/videos/g09-cocos-training/v2/01.mp4',
        durationSeconds: 626,
        mediaVersion: 'g09-cocos-training-v2-01',
      }),
      videoStep({
        key: 'g09-cocos-alphabet',
        title: 'Alphabet',
        titleZh: '字母',
        assetUrl: '/videos/g09-cocos-training/v2/02.mp4',
        durationSeconds: 670,
        mediaVersion: 'g09-cocos-training-v2-02',
      }),
      videoStep({
        key: 'g09-cocos-dialogue',
        title: 'Dialogue',
        titleZh: '对话',
        assetUrl: '/videos/g09-cocos-training/v2/03.mp4',
        durationSeconds: 1129,
        mediaVersion: 'g09-cocos-training-v2-03',
      }),
      videoStep({
        key: 'g09-cocos-phonics',
        title: 'Phonics',
        titleZh: '自然拼读',
        assetUrl: '/videos/g09-cocos-training/v2/04.mp4',
        durationSeconds: 687,
        mediaVersion: 'g09-cocos-training-v2-04',
      }),
      videoStep({
        key: 'g09-cocos-reading',
        title: 'Reading',
        titleZh: '阅读',
        assetUrl: '/videos/g09-cocos-training/v2/05.mp4',
        durationSeconds: 780,
        mediaVersion: 'g09-cocos-training-v2-05',
      }),
      videoStep({
        key: 'g09-cocos-word-sentence',
        title: 'Word & Sentence',
        titleZh: '单词与句子',
        assetUrl: '/videos/g09-cocos-training/v2/06.mp4',
        durationSeconds: 772,
        mediaVersion: 'g09-cocos-training-v2-06',
      }),
      videoStep({
        key: 'g09-cocos-review-carnival',
        title: 'Review Carnival',
        titleZh: '复习嘉年华',
        assetUrl: '/videos/g09-cocos-training/v2/07.mp4',
        durationSeconds: 1004,
        mediaVersion: 'g09-cocos-training-v2-07',
      }),
      publishedQuizStep({
        key: 'g09-knowledge-check',
        title: 'Complete the Cocos knowledge check',
        configVersion: 'g09-quiz-2026-07-23',
        quizBankKey: 'cocos-training',
        questionSetVersion: 'course-630-2026-07',
        expectedQuestionCount: 5,
      }),
    ],
    rules: [allStepsRule('请先按顺序完整观看全部视频并完成知识检查。')],
  },
  {
    code: 'G09',
    title: 'SET Teaching Fundamentals',
    why: 'Learn the fundamentals of SET teaching.',
    whatToDo:
      'Watch the in-platform Mock video slot and complete the five-question Mock check.',
    completionStandard:
      'The Mock video is watched in full and the five-question check reaches 80%.',
    benefit: 'You understand the SET teaching foundation.',
    priority: 'P1',
    score: 5,
    stage: 'ADVANCE',
    sequence: 9,
    estimatedMinutes: 10,
    contentStatus: 'READY',
    allowRetry: true,
    kind: 'FIXED_GROWTH',
    steps: [
      videoStep({
        key: 'g10-set-fundamentals-video',
        title: 'SET Teaching Fundamentals · Mock media slot',
        titleZh: 'SET 教学基础 · Mock 视频槽',
        assetUrl: '/videos/g03-platform-policies/v2/01.mp4',
        durationSeconds: 163,
        mediaVersion: 'g10-set-fundamentals-mock-v2',
      }),
      publishedQuizStep({
        key: 'g10-knowledge-check',
        title: 'Complete the SET Teaching Fundamentals Mock check',
        configVersion: 'g10-quiz-mock-2026-07-24',
        quizBankKey: 'set-fundamentals',
        questionSetVersion: 'mock-set-fundamentals-2026-07-v1',
        expectedQuestionCount: 5,
      }),
    ],
    rules: [allStepsRule('请先完整观看站内 Mock 视频并完成 5 道练习。')],
  },
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
    ['P-FB-NEGATIVE', 'feedback-topic-learning', 'Improve a Teaching Skill', 4],
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

async function assertCurrentSharedCatalog(client: Client): Promise<void> {
  const result = await client.query<{
    taskCode: string;
    title: string | null;
    score: string | null;
  }>(
    `
      SELECT
        template_id AS "taskCode",
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
    if (actual?.title === task.title && Number(actual.score) === task.score) {
      return [];
    }
    return [
      `${task.code}: expected ${task.title}/${task.score}, got ` +
        `${actual?.title ?? 'missing'}/${actual?.score ?? 'missing'}`,
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
    `,
    [
      executionId,
      templateRowId,
      task.code,
      {
        estimatedMinutes: task.estimatedMinutes,
        allowRetry: task.allowRetry,
        contentStatus: task.contentStatus,
        contentVersion: '2026-07-30',
        pendingReason: task.pendingReason ?? null,
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
    await retireLegacyPersonalizedCatalog(client, allowPublicWrites);
    await retireMergedSharedExecutions(client);
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
