export interface SourceFreshness {
  source: 'LIVE' | 'CACHED' | 'UNAVAILABLE';
  sourceUpdatedAt: string | null;
  fetchedAt: string | null;
  stale: boolean;
}

export type GraduationState = 'IN_CAMP' | 'GRADUATED';
export type TeacherOnlineStatus = 'NEW' | 'EXISTING' | 'LEFT' | 'BLOCKED';
export type GoldStatus = 'NOT_GOLD' | 'GOLD';
export type TeacherSourceStatus = 'CONFIRMED' | 'SOURCE_MISSING';

export interface TeacherProfileResponse {
  teacherId: string;
  email: string;
  name: string;
  timezone: string | null;
  campDay: number | null;
  totalCampDays: number;
  graduationState: GraduationState | null;
  freshness: SourceFreshness;
}

export type ExternalReviewStatus =
  'WAITING' | 'IN_REVIEW' | 'APPROVED' | 'NEEDS_CHANGES' | 'UNAVAILABLE';

export interface G01ReviewResponse {
  tesolStatus: ExternalReviewStatus;
  externalStatusesComplete: boolean;
  freshness: SourceFreshness;
}

export interface TideSummaryAvailableResponse {
  available: true;
  onlineStatus: TeacherOnlineStatus;
  rawTotalScore: number;
  publicTotalScore: number;
  graduationState: GraduationState;
  graduationQualified: boolean;
  graduationQualifiedAt: string | null;
  graduationScoreLocked: number | null;
  goldQualified: boolean;
  goldStatus: GoldStatus;
  goldQualifiedAt: string | null;
  graduationThreshold: number;
  goldThreshold: number;
  mandatoryTaskCompletedCount: number;
  mandatoryTaskTotalCount: number;
  scoreRuleVersion: string;
  calculatedAt: string;
  dimensions: ScoreDimensionResponse[];
  availableScore: AvailableScoreResponse | null;
  source: {
    teacherSourceStatus: TeacherSourceStatus;
  };
  freshness: SourceFreshness;
}

export interface TideSummaryEmptyResponse {
  available: false;
  reason: 'NO_GROWTH_DATA';
  freshness: SourceFreshness;
}

export type TideSummaryResponse =
  TideSummaryAvailableResponse | TideSummaryEmptyResponse;

export interface AvailableScoreItemResponse {
  taskCode: string;
  title: string | null;
  score: number;
  taskStatus: string;
}

export interface AvailableScoreResponse {
  score: number;
  items: AvailableScoreItemResponse[];
}

export interface ScoreComponentResponse {
  code: string;
  unitCount: number;
  pointsPerUnit: number | null;
  score: number;
  sourceScope: 'LESSON' | 'TEACHER' | 'TASK';
  sourceMetric: string | null;
}

export interface ScoreDimensionResponse {
  code:
    | 'USER_FEEDBACK'
    | 'RELIABILITY'
    | 'CLASS_QUALITY'
    | 'CAPACITY'
    | 'NEW_TEACHER_TASK';
  score: number;
  scoreRuleVersion: string;
  projectionRevision: number;
  calculatedAt: string;
  components: ScoreComponentResponse[];
}

export interface CourseFactResponse {
  /** Compatibility display key; never a source lesson ID or database locator. */
  lessonId: string;
  sourceRegion: 'dom' | 'ovs';
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
  dimensions: CourseScoreDimensionResponse[];
}

export interface CourseScoreComponentResponse {
  code: string;
  score: number;
  pointsPerUnit: number | null;
  awarded: boolean;
  evidenceStatus: string;
}

export interface CourseScoreDimensionResponse {
  code: 'USER_FEEDBACK' | 'RELIABILITY' | 'CLASS_QUALITY';
  score: number;
  evidenceStatus: string;
  evidenceCoverage: string | null;
  components: CourseScoreComponentResponse[];
}

export interface CourseListResponse {
  items: CourseFactResponse[];
  page: number;
  pageSize: number;
  totalCount: number;
  freshness: SourceFreshness;
}

export interface NotificationResponse {
  sourceNotificationId: string;
  source: 'EXTERNAL' | 'SYSTEM';
  typeCode: string | null;
  title: string;
  body: string;
  relatedTaskCode: string | null;
  relatedTaskInstanceId: string | null;
  actionType: 'TASK_DETAIL' | 'MY_TIDE' | 'TASKS' | 'HELP' | 'ACCOUNT' | null;
  actionTarget: string | null;
  actionAvailable: boolean;
  expiresAt: string | null;
  expired: boolean;
  issuedAt: string;
  read: boolean;
  clicked: boolean;
}

export interface NotificationListResponse {
  items: NotificationResponse[];
  totalCount: number;
  unreadCount: number;
  nextCursor: string | null;
  freshness: SourceFreshness;
}
