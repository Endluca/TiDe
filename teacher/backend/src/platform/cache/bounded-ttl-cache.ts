interface CacheEntry<Value> {
  value: Value;
  expiresAt: number;
}

export class BoundedTtlCache<Value> {
  private readonly entries = new Map<string, CacheEntry<Value>>();

  constructor(
    private readonly maxSize: number,
    private readonly ttlMs: number,
  ) {}

  get(key: string): Value | undefined {
    const entry = this.entries.get(key);
    if (!entry) return undefined;
    if (entry.expiresAt <= Date.now()) {
      this.entries.delete(key);
      return undefined;
    }
    this.entries.delete(key);
    this.entries.set(key, entry);
    return entry.value;
  }

  set(key: string, value: Value): void {
    this.entries.delete(key);
    this.entries.set(key, {
      value,
      expiresAt: Date.now() + this.ttlMs,
    });
    while (this.entries.size > this.maxSize) {
      const oldestKey = this.entries.keys().next().value;
      if (!oldestKey) break;
      this.entries.delete(oldestKey);
    }
  }

  get size(): number {
    return this.entries.size;
  }
}
