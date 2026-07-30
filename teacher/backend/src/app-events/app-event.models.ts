export interface AppEventAccepted {
  accepted: true;
  eventId: string;
}

export interface AppEventBatchAccepted {
  acceptedEventIds: string[];
  failures: Array<{
    index: number;
    code: string;
  }>;
}

export interface SystemAppEventInput {
  eventName: string;
  eventId?: string;
  accountId?: string | null;
  anonymousActorId?: string | null;
  sessionId: string;
  taskAssignmentId?: string | null;
  properties?: Record<string, unknown>;
  occurredAt?: Date;
}
