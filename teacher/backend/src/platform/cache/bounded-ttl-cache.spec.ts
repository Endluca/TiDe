import { BoundedTtlCache } from './bounded-ttl-cache';

describe('BoundedTtlCache', () => {
  afterEach(() => jest.restoreAllMocks());

  it('evicts the least recently used version and expires stale entries', () => {
    let now = 1_000;
    jest.spyOn(Date, 'now').mockImplementation(() => now);
    const cache = new BoundedTtlCache<number>(2, 100);
    cache.set('v1', 1);
    cache.set('v2', 2);
    expect(cache.get('v1')).toBe(1);
    cache.set('v3', 3);

    expect(cache.get('v2')).toBeUndefined();
    expect(cache.get('v1')).toBe(1);
    now = 1_101;
    expect(cache.get('v1')).toBeUndefined();
  });
});
