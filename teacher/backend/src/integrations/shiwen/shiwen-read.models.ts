export type ShiwenDataMode = 'MIXED' | 'REAL' | 'MOCK';

export interface ShiwenTeacherIdentity {
  teacherId: string;
  campEnrollmentId: string;
  name: string;
  timezone: string | null;
  campDay: number | null;
  graduationState: string | null;
  dataMode: ShiwenDataMode;
  sourceUpdatedAt: string;
}

export type ShiwenScoreDimensionCode =
  | 'USER_FEEDBACK'
  | 'RELIABILITY'
  | 'CLASS_QUALITY'
  | 'CAPACITY'
  | 'NEW_TEACHER_TASK';

export type ShiwenScoreSourceScope = 'LESSON' | 'TEACHER' | 'TASK';

export interface ShiwenScoreComponent {
  code: string;
  unitCount: number;
  pointsPerUnit: number | null;
  score: number;
  sourceScope: ShiwenScoreSourceScope;
  sourceMetric: string | null;
  lessonAttributedCount: number;
  lessonAttributedScore: number;
  unattributedScore: number;
  reconciliationStatus: string;
}

export interface ShiwenScoreDimension {
  code: ShiwenScoreDimensionCode;
  score: number;
  scoreRuleVersion: string;
  projectionRevision: number;
  calculatedAt: string;
  components: ShiwenScoreComponent[];
}

export interface ShiwenTeacherScorecard {
  teacherId: string;
  campEnrollmentId: string;
  rawTotalScore: number;
  publicTotalScore: number;
  graduationState: string;
  graduationQualified: boolean;
  goldQualified: boolean;
  graduationThreshold: number;
  goldThreshold: number;
  mandatoryTaskCompletedCount: number;
  mandatoryTaskTotalCount: number;
  scoreRuleVersion: string;
  calculatedAt: string;
  dimensions: ShiwenScoreDimension[];
}

export type ShiwenLessonScoreDimensionCode =
  'USER_FEEDBACK' | 'RELIABILITY' | 'CLASS_QUALITY';

export interface ShiwenLessonScoreComponent {
  code: string;
  score: number;
  pointsPerUnit: number | null;
  awarded: boolean;
  evidenceStatus: string;
}

export interface ShiwenLessonScoreDimension {
  code: ShiwenLessonScoreDimensionCode;
  score: number;
  evidenceStatus: string;
  evidenceCoverage: string | null;
  components: ShiwenLessonScoreComponent[];
}

export interface ShiwenLessonScore {
  teacherId: string;
  lessonId: string;
  lessonSequence: number;
  lessonCount: number;
  sourceAppointId: string;
  scheduledStartAt: string | null;
  lessonLocalDate: string | null;
  lessonLocalTime: string | null;
  lifecycleStatus: string;
  validForScoring: boolean;
  evidenceStatus: string;
  lessonTotalScore: number;
  scoreRuleVersion: string;
  updatedAt: string;
  facts: {
    late: boolean | null;
    earlyLeave: boolean | null;
    falseEarlyLeave: boolean | null;
    positiveFeedback: boolean | null;
    favorited: boolean | null;
    rebooked: boolean | null;
    cameraOff: boolean | null;
    cpuUsageHigh: boolean | null;
    networkDelayHigh: boolean | null;
    peak: boolean | null;
  };
  dimensions: ShiwenLessonScoreDimension[];
}
