export type VideoPrefetchTrigger = 'NEW_RELEASE' | 'CDN_CHANGE' | 'CAMP_LAUNCH';

export type VideoPrefetchRunStatus =
  'PENDING' | 'SUBMITTED' | 'POLLING' | 'COMPLETE' | 'FAILED';

export interface VideoPrefetchTask {
  taskId: string;
  url?: string;
  status: string;
  process?: string;
  creationTime?: string;
  description?: string;
}

export interface VideoPrefetchTaskGroup {
  taskId: string;
  tasks: VideoPrefetchTask[];
}

export interface VideoPrefetchProvider {
  submit(
    urls: string[],
    options: { area: 'overseas'; l2Preload: boolean },
  ): Promise<{ taskIds: string[]; requestId?: string }>;
  describe(
    taskIds: string[],
    domainName: string,
  ): Promise<VideoPrefetchTaskGroup[]>;
}

export interface VideoPrefetchRun {
  runId: string;
  idempotencyKey: string;
  trigger: VideoPrefetchTrigger;
  eventId: string;
  manifestVersion: string;
  urls: string[];
  taskIds: string[];
  attempts: number;
  status: VideoPrefetchRunStatus;
  createdAt: string;
  updatedAt: string;
  startedAt?: string;
  completedAt?: string;
  lastError?: string;
  requestId?: string;
}

export interface VideoPrefetchLogEntry {
  time: string;
  runId: string;
  idempotencyKey: string;
  trigger: VideoPrefetchTrigger;
  eventId: string;
  manifestVersion: string;
  status: string;
  url?: string;
  taskId?: string;
  process?: string;
  attempt?: number;
  message?: string;
}
