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
export type TaskStepType =
  | 'VIDEO'
  | 'DOCUMENT'
  | 'QUIZ'
  | 'CHECKLIST'
  | 'UPLOAD'
  | 'DEVICE_CHECK'
  | 'EXTERNAL_TRAINING'
  | 'CUSTOM';

export interface TaskDisplayMetadata {
  stageKey: string | null;
  sequence: number | null;
  points: number;
  estimatedMinutes: number | null;
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
  dataOrigin:
    'REAL' | 'DERIVED_REAL' | 'MOCK' | 'MOCK_SIMULATION' | 'MOCK_PROXY';
}

export interface TaskListResponse {
  items: TaskSummary[];
  contexts: TaskContext[];
}

export interface TaskValidationResponse {
  status: 'PENDING' | 'UNDER_REVIEW' | 'PASSED' | 'FAILED' | 'ERROR';
  resultCode: string | null;
  teacherMessage: string | null;
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
    details?: Record<string, unknown>;
  };
  validation?: TaskValidationResponse;
}

export interface TaskStepContext {
  stepKey: string;
  position: number;
  type: TaskStepType;
  title: string;
  config: Record<string, unknown>;
}

export interface TaskTeacherSafeFact {
  label: string;
  labelZh: string | null;
  value: string;
  valueZh: string | null;
}

export interface TaskRelatedCourse {
  lessonId: string;
  label: string;
  labelZh: string | null;
  summary: string;
  summaryZh: string | null;
  occurredAt: string | null;
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
  content: {
    title: string;
    why: string;
    whatToDo: string;
    completionStandard: string;
    outcome: string;
    language: string;
  };
  steps: TaskStepContext[];
  progress: {
    currentStepKey: string | null;
    percent: number;
    steps: Array<{
      stepKey: string;
      status: 'NOT_STARTED' | 'IN_PROGRESS' | 'COMPLETED' | 'FAILED';
      percent: number;
      details: Record<string, unknown>;
    }>;
  };
  execution: {
    contentStatus: 'READY' | 'PENDING';
    contentVersion: string | null;
    pendingReason: string | null;
  };
  capabilities: TaskStepType[];
  assignment: {
    assignmentId: string;
    teacherSafeReason: string;
    teacherSafeFacts: TaskTeacherSafeFact[];
    relatedCourses: TaskRelatedCourse[];
    reminderNotificationId: string | null;
    priority: 'P0' | 'P1' | 'P2' | 'P3';
    dueAt: string | null;
  };
  availableAt: string | null;
  dueAt: string | null;
  completedAt: string | null;
  dataOrigin:
    'REAL' | 'DERIVED_REAL' | 'MOCK' | 'MOCK_SIMULATION' | 'MOCK_PROXY';
}
