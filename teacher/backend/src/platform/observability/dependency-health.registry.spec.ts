import { DependencyHealthRegistry } from './dependency-health.registry';

describe('DependencyHealthRegistry', () => {
  it('uses the configured fallback before the first real call', () => {
    const registry = new DependencyHealthRegistry();

    expect(registry.snapshot('fileStorage', 'configured')).toEqual({
      status: 'configured',
      lastCheckedAt: null,
      lastSuccessAt: null,
      lastFailureAt: null,
      lastLatencyMs: null,
      errorCode: null,
    });
  });

  it('keeps the last success and failure timestamps', () => {
    const registry = new DependencyHealthRegistry();

    registry.recordSuccess('aiGateway', Date.now());
    registry.recordFailure('aiGateway', 'AI_GATEWAY_UNAVAILABLE', Date.now());
    const snapshot = registry.snapshot('aiGateway', 'configured');

    expect(snapshot.status).toBe('error');
    expect(typeof snapshot.lastCheckedAt).toBe('string');
    expect(typeof snapshot.lastSuccessAt).toBe('string');
    expect(typeof snapshot.lastFailureAt).toBe('string');
    expect(typeof snapshot.lastLatencyMs).toBe('number');
    expect(snapshot.errorCode).toBe('AI_GATEWAY_UNAVAILABLE');
  });
});
