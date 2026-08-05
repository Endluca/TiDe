/**
 * 后端开工前冻结的公共类型合同，不包含业务实现。
 * OpenAPI 是 HTTP 合同真相源；本文件供嘉荷前端和任务扩展开发时直接引用或生成类型。
 */

export type DataOrigin =
  'REAL' | 'DERIVED_REAL' | 'MOCK' | 'MOCK_SIMULATION' | 'MOCK_PROXY';

export interface RegisterRequest {
  email: string;
  teacherId: string;
  password: string;
}

export interface RegistrationAccepted {
  status: 'VERIFICATION_REQUIRED';
  verificationEmailSent: boolean;
}

export interface ConfirmEmailRequest {
  token: string;
}

export interface EmailVerificationResponse {
  status: 'VERIFIED' | 'ALREADY_VERIFIED';
}

export interface ResendVerificationRequest {
  email: string;
}

export interface ResendAccepted {
  accepted: true;
}

export interface LoginRequest {
  email: string;
  password: string;
}

export interface RefreshSessionRequest {
  refreshToken: string;
}

export interface AuthTokenPair {
  tokenType: 'Bearer';
  accessToken: string;
  accessTokenExpiresIn: number;
  refreshToken: string;
  refreshTokenExpiresAt: string;
}

export interface PasswordResetRequest {
  email: string;
}

export interface ConfirmPasswordResetRequest {
  token: string;
  newPassword: string;
}

export interface PasswordResetCompleted {
  status: 'PASSWORD_UPDATED';
}

export interface TeacherProfile {
  teacherId: string;
  email: string;
  name: string;
  timezone: string | null;
  campDay: number | null;
  totalCampDays: number;
  graduationState: string | null;
  freshness: SourceFreshness;
}

export type ExternalReviewStatus =
  'WAITING' | 'IN_REVIEW' | 'APPROVED' | 'NEEDS_CHANGES' | 'UNAVAILABLE';

export interface G01Review {
  selfIntroStatus: ExternalReviewStatus;
  tesolStatus: ExternalReviewStatus;
  externalStatusesComplete: boolean;
  freshness: SourceFreshness;
}

export interface Notification {
  sourceNotificationId: string;
  source: 'EXTERNAL' | 'SYSTEM';
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
  items: Notification[];
  totalCount: number;
  unreadCount: number;
  nextCursor: string | null;
  freshness: SourceFreshness;
}

export type TaskKind = 'FIXED_GROWTH' | 'PERSONALIZED_IMPROVEMENT';
export type TaskStatus =
  | 'ASSIGNED'
  | 'VIEWED'
  | 'IN_PROGRESS'
  | 'SUBMITTED'
  | 'UNDER_REVIEW'
  | 'COMPLETED'
  | 'FAILED'
  | 'EXPIRED'
  | 'WAIVED'
  | 'CANCELLED';

export type StepType =
  | 'VIDEO'
  | 'DOCUMENT'
  | 'QUIZ'
  | 'CHECKLIST'
  | 'UPLOAD'
  | 'DEVICE_CHECK'
  | 'EXTERNAL_TRAINING'
  | 'CUSTOM';

export interface SharedAssignmentContext {
  assignmentId: string;
  teacherSafeReason: string;
  teacherSafeFacts: Array<{
    label: string;
    labelZh: string | null;
    value: string;
    valueZh: string | null;
  }>;
  relatedCourses: Array<{
    lessonId: string;
    label: string;
    labelZh: string | null;
    summary: string;
    summaryZh: string | null;
    occurredAt: string | null;
  }>;
  reminderNotificationId: string | null;
  priority: 'P0' | 'P1' | 'P2' | 'P3';
  dueAt: string | null;
}

export interface TeacherVisibleTaskContent {
  title: string;
  why: string;
  whatToDo: string;
  completionStandard: string;
  outcome: string;
  language: string;
}

export interface TaskStep {
  stepKey: string;
  position: number;
  type: StepType;
  title: string;
  config: Record<string, unknown>;
}

export interface TaskProgress {
  currentStepKey: string | null;
  percent: number;
  steps: Array<{
    stepKey: string;
    status: 'NOT_STARTED' | 'IN_PROGRESS' | 'COMPLETED' | 'FAILED';
    percent: number;
  }>;
}

export interface TaskSummary {
  taskInstanceId: string;
  taskCode: string;
  kind: TaskKind;
  status: TaskStatus;
  stateVersion: number;
  display: TaskDisplayMetadata;
  title: string;
  priority: 'P0' | 'P1' | 'P2' | 'P3';
  availableAt: string | null;
  dueAt: string | null;
  completedAt: string | null;
  dataOrigin: DataOrigin;
}

export interface TaskDisplayMetadata {
  stageKey: string | null;
  sequence: number | null;
  points: number;
  estimatedMinutes: number | null;
}

export interface TaskListResponse {
  items: TaskSummary[];
}

export interface TaskContext {
  taskInstanceId: string;
  taskCode: string;
  kind: TaskKind;
  templateVersion: number;
  executionContractVersion: string;
  status: TaskStatus;
  stateVersion: number;
  display: TaskDisplayMetadata;
  content: TeacherVisibleTaskContent;
  steps: TaskStep[];
  progress: TaskProgress;
  capabilities: StepType[];
  assignment: SharedAssignmentContext;
  availableAt: string | null;
  dueAt: string | null;
  completedAt: string | null;
  dataOrigin: DataOrigin;
}

export interface KuozhiLaunchResponse {
  provider: 'KUOZHI';
  dataMode: 'REAL' | 'SAMPLE_DRY_RUN';
  integrationStatus: 'ACTIVE' | 'PARTIAL' | 'MAPPING_ONLY';
  mappingVersion: number;
  courses: Array<{
    courseId: string;
    title: string | null;
    embedMode: 'IFRAME';
    launchUrl: string;
  }>;
}

export interface KuozhiProgressResponse {
  provider: 'KUOZHI';
  dataMode: 'REAL' | 'SAMPLE_DRY_RUN';
  integrationStatus: 'ACTIVE' | 'PARTIAL' | 'MAPPING_ONLY';
  mappingVersion: number;
  syncStatus: 'NOT_SYNCED' | 'AVAILABLE' | 'PARTIAL' | 'NO_DATA';
  refreshedAt: string | null;
  courses: Array<{
    courseId: string;
    title: string;
    sourceAvailable: boolean;
    percent: number | null;
    completed: boolean;
    tasks: Array<{
      courseTaskId: string;
      title: string;
      type: 'VIDEO' | 'TESTPAPER';
      required: boolean;
      sourceStatus: 'AVAILABLE' | 'MISSING' | 'INVALID';
      percent: number | null;
      score: number | null;
      normalizedScorePercent: number | null;
      passScorePercent: number | null;
      testTimes: number | null;
      completed: boolean;
    }>;
  }>;
  completion: {
    enabled: boolean;
    completed: boolean;
    reasonCode: string;
  };
  assignment: {
    status: TaskStatus;
    stateVersion: number;
    stateUpdated: boolean;
  };
}

export interface MutationMeta {
  commandId: string;
  expectedStateVersion: number;
}

export interface StartTaskRequest extends MutationMeta {}

export interface ViewTaskRequest extends MutationMeta {}

export interface SaveProgressRequest extends MutationMeta {
  stepKey: string;
  percent: number;
  progress: Record<string, unknown>;
}

export interface StepOutput {
  stepKey: string;
  outputType:
    | 'QUIZ'
    | 'CHECKLIST'
    | 'FILE'
    | 'DEVICE_CHECK'
    | 'EXTERNAL_PROOF'
    | 'CUSTOM';
  value: Record<string, unknown>;
}

export interface SubmitTaskRequest extends MutationMeta {
  attemptId: string;
  outputs: StepOutput[];
}

export interface RetryTaskRequest extends MutationMeta {
  reasonCode?: string;
}

export interface TaskMutationResponse {
  accepted: true;
  taskInstanceId: string;
  status: TaskStatus;
  stateVersion: number;
  step?: {
    stepKey: string;
    status: 'NOT_STARTED' | 'IN_PROGRESS' | 'COMPLETED' | 'FAILED';
    percent: number;
    result?: Record<string, unknown>;
  };
  validation?: {
    status: 'PENDING' | 'UNDER_REVIEW' | 'PASSED' | 'FAILED' | 'ERROR';
    resultCode: string | null;
    teacherMessage: string | null;
  };
}

export interface UploadIntentRequest {
  taskInstanceId: string;
  stepKey: string;
  filename: string;
  mimeType: string;
  sizeBytes: number;
  sha256: string;
}

export interface UploadIntentResponse {
  fileId: string;
  uploadMethod: 'LOCAL_STREAM' | 'OSS_POST_FORM';
  uploadUrl: string;
  expiresAt: string;
  requiredHeaders: Record<string, string>;
  requiredFields: Record<string, string>;
}

export interface CompleteUploadRequest {
  sha256: string;
}

export interface FileObject {
  fileId: string;
  status: 'PENDING' | 'READY' | 'QUARANTINED' | 'DELETED';
  mimeType: string;
  sizeBytes: number;
  sha256: string;
}

export interface SourceFreshness {
  source: 'LIVE' | 'CACHED' | 'UNAVAILABLE';
  sourceUpdatedAt: string | null;
  fetchedAt: string | null;
  stale: boolean;
}

export interface FaqConversationCreated {
  conversationId: string;
  status: 'OPEN';
  startedAt: string;
}

export interface AskFaqRequest {
  message: string;
}

export interface FaqSource {
  position: number;
  title: string;
  section: string;
  question: string | null;
  category: string | null;
  url: string | null;
}

export interface FaqFeedback {
  resolved: boolean;
  reasonCode: string | null;
}

export interface FaqMessage {
  id: string;
  role: 'TEACHER' | 'ASSISTANT' | 'SYSTEM';
  body: string;
  faqHit: boolean;
  reasonCode:
    | 'FAQ_MATCHED'
    | 'FAQ_CLARIFICATION_NEEDED'
    | 'FAQ_NOT_FOUND'
    | 'FAQ_AI_UNAVAILABLE'
    | 'FAQ_AI_RESPONSE_INVALID'
    | null;
  sources: FaqSource[];
  feedback: FaqFeedback | null;
  createdAt: string;
}

export interface FaqConversation {
  conversationId: string;
  status: 'OPEN' | 'CLOSED' | 'ARCHIVED';
  startedAt: string;
  messages: FaqMessage[];
}

export interface FaqAnswerResponse {
  conversationId: string;
  teacherMessage: FaqMessage;
  answer: FaqMessage;
}

export interface FaqFeedbackRequest {
  resolved: boolean;
  reasonCode?: string;
}

export interface FaqFeedbackResponse {
  messageId: string;
  resolved: boolean;
  reasonCode: string | null;
}

export interface CreateAppEvent {
  eventName: string;
  eventId: string;
  taskInstanceId?: string;
  properties: Record<string, unknown>;
  occurredAt: string;
}

export interface AppEventAccepted {
  accepted: true;
  eventId: string;
}

export interface ApiError {
  code: string;
  message: string;
  requestId: string;
  retryable: boolean;
  details?: Record<string, unknown>;
}
