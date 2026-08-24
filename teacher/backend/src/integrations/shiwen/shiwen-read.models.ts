export type ShiwenDataMode = 'MIXED' | 'REAL' | 'MOCK';
export type ShiwenGraduationState = 'IN_CAMP' | 'GRADUATED';
export type ShiwenCourseSourceRegion = 'dom' | 'ovs';
export type ShiwenTeacherOnlineStatus = 'NEW' | 'EXISTING' | 'LEFT' | 'BLOCKED';
export type ShiwenGoldStatus = 'NOT_GOLD' | 'GOLD';
export type ShiwenTeacherSourceStatus = 'CONFIRMED' | 'SOURCE_MISSING';

export interface ShiwenTeacherIdentity {
  teacherId: string;
  campEnrollmentId: string;
  name: string;
  timezone: string | null;
  campDay: number | null;
  graduationState: ShiwenGraduationState | null;
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
  onlineStatus: ShiwenTeacherOnlineStatus;
  rawTotalScore: number;
  publicTotalScore: number;
  graduationState: ShiwenGraduationState;
  graduationQualified: boolean;
  graduationQualifiedAt: string | null;
  graduationScoreLocked: number | null;
  goldQualified: boolean;
  goldStatus: ShiwenGoldStatus;
  goldQualifiedAt: string | null;
  graduationThreshold: number;
  goldThreshold: number;
  mandatoryTaskCompletedCount: number;
  mandatoryTaskTotalCount: number;
  scoreRuleVersion: string;
  calculatedAt: string;
  dimensions: ShiwenScoreDimension[];
  source: {
    teacherSourceStatus: ShiwenTeacherSourceStatus;
  };
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
  /** Compatibility display key; never a source lesson ID or database locator. */
  lessonId: string;
  sourceRegion: ShiwenCourseSourceRegion;
  sourceAppointId: string;
  participationSeq: number;
  lessonSequence: number;
  lessonCount: number;
  scheduledStartAt: string | null;
  lessonLocalDate: string | null;
  lessonLocalTime: string | null;
  lifecycleStatus: string;
  validForScoring: boolean;
  evidenceStatus: string;
  lessonTotalScore: number;
  scoreRuleVersion: string | null;
  updatedAt: string;
  facts: {
    late: boolean | null;
    earlyLeave: boolean | null;
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
