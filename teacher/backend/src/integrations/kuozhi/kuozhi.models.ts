import type {
  KuozhiCourseMapping,
  KuozhiPassScoreSource,
} from './kuozhi-course.config';

export type KuozhiDataMode = 'REAL' | 'SAMPLE_DRY_RUN';
export type KuozhiSyncStatus =
  'NOT_SYNCED' | 'AVAILABLE' | 'PARTIAL' | 'NO_DATA';

export interface KuozhiResolvedMapping {
  mappingVersion: number;
  taskCode: string;
  dataMode: KuozhiDataMode;
  queryTeacherId: string;
  mapping: KuozhiCourseMapping;
}

export interface KuozhiLaunchCourse {
  courseId: string;
  title: string | null;
  embedMode: 'IFRAME' | 'NEW_WINDOW';
  launchUrl: string;
}

export interface KuozhiLaunchResponse {
  provider: 'KUOZHI';
  dataMode: KuozhiDataMode;
  integrationStatus: 'ACTIVE' | 'PARTIAL' | 'MAPPING_ONLY';
  mappingVersion: number;
  courses: KuozhiLaunchCourse[];
}

export interface KuozhiProgressTask {
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
}

export interface KuozhiProgressCourse {
  courseId: string;
  title: string;
  sourceAvailable: boolean;
  percent: number | null;
  completed: boolean;
  tasks: KuozhiProgressTask[];
}

export interface KuozhiProgressCore {
  provider: 'KUOZHI';
  dataMode: KuozhiDataMode;
  integrationStatus: 'ACTIVE' | 'PARTIAL' | 'MAPPING_ONLY';
  mappingVersion: number;
  syncStatus: KuozhiSyncStatus;
  refreshedAt: string | null;
  courses: KuozhiProgressCourse[];
  completion: {
    enabled: boolean;
    completed: boolean;
    reasonCode:
      | 'NOT_SYNCED'
      | 'COMPLETED'
      | 'COMPLETION_DISABLED'
      | 'NO_DATA'
      | 'REQUIRED_TASK_MISSING'
      | 'SOURCE_VALUE_INVALID'
      | 'REQUIREMENTS_INCOMPLETE';
  };
}

export interface KuozhiProgressResponse extends KuozhiProgressCore {
  assignment: {
    status: string;
    stateVersion: number;
    stateUpdated: boolean;
  };
}

export interface KuozhiPassScoreReference {
  key: string;
  source: Extract<KuozhiPassScoreSource, { kind: 'QUIZ_BANK' }>;
}

export function kuozhiPassScoreKey(
  bankKey: string,
  questionSetVersion: string,
): string {
  return `${bankKey}\u0000${questionSetVersion}`;
}
