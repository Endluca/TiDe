import { encodeCompatibilityLessonId } from './course-participation-identity';

describe('course participation identity', () => {
  it('encodes a deterministic compatibility display key', () => {
    const identity = {
      sourceRegion: 'dom' as const,
      sourceAppointId: 'appoint:with/slashes',
      participationSeq: 2,
    };

    expect(encodeCompatibilityLessonId(identity)).toBe(
      encodeCompatibilityLessonId({ ...identity }),
    );
    expect(encodeCompatibilityLessonId(identity)).toMatch(
      /^participation:v1:[A-Za-z0-9_-]+$/,
    );
  });

  it('does not collapse different regions or teacher participations', () => {
    const base = {
      sourceAppointId: 'appoint-001',
      participationSeq: 1,
    };
    const keys = new Set([
      encodeCompatibilityLessonId({ sourceRegion: 'dom', ...base }),
      encodeCompatibilityLessonId({ sourceRegion: 'ovs', ...base }),
      encodeCompatibilityLessonId({
        sourceRegion: 'dom',
        ...base,
        participationSeq: 2,
      }),
    ]);

    expect(keys.size).toBe(3);
  });
});
