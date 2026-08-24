export type CourseSourceRegion = 'dom' | 'ovs';

export interface CourseParticipationIdentity {
  sourceRegion: CourseSourceRegion;
  sourceAppointId: string;
  participationSeq: number;
}

const COMPATIBILITY_KEY_PREFIX = 'participation:v1:';

/**
 * Legacy UI/support-ticket compatibility only. Database reads, cache identity,
 * pagination and authorization must use the three identity fields directly.
 */
export function encodeCompatibilityLessonId(
  identity: CourseParticipationIdentity,
): string {
  const payload = JSON.stringify([
    identity.sourceRegion,
    identity.sourceAppointId,
    identity.participationSeq,
  ]);
  return `${COMPATIBILITY_KEY_PREFIX}${Buffer.from(payload, 'utf8').toString('base64url')}`;
}
