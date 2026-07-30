import { Injectable } from '@nestjs/common';

export type ExternalDependency = 'fileStorage' | 'aiGateway' | 'mail';
export type ExternalDependencyStatus =
  'ok' | 'error' | 'configured' | 'disabled' | 'not_configured';

export interface ExternalDependencyHealth {
  status: ExternalDependencyStatus;
  lastCheckedAt: string | null;
  lastSuccessAt: string | null;
  lastFailureAt: string | null;
  lastLatencyMs: number | null;
  errorCode: string | null;
}

interface MutableDependencyHealth {
  status: 'ok' | 'error';
  lastCheckedAt: Date;
  lastSuccessAt: Date | null;
  lastFailureAt: Date | null;
  lastLatencyMs: number;
  errorCode: string | null;
}

@Injectable()
export class DependencyHealthRegistry {
  private readonly states = new Map<
    ExternalDependency,
    MutableDependencyHealth
  >();

  recordSuccess(dependency: ExternalDependency, startedAt: number): void {
    const now = new Date();
    const previous = this.states.get(dependency);
    this.states.set(dependency, {
      status: 'ok',
      lastCheckedAt: now,
      lastSuccessAt: now,
      lastFailureAt: previous?.lastFailureAt ?? null,
      lastLatencyMs: Math.max(0, Date.now() - startedAt),
      errorCode: null,
    });
  }

  recordFailure(
    dependency: ExternalDependency,
    errorCode: string,
    startedAt: number,
  ): void {
    const now = new Date();
    const previous = this.states.get(dependency);
    this.states.set(dependency, {
      status: 'error',
      lastCheckedAt: now,
      lastSuccessAt: previous?.lastSuccessAt ?? null,
      lastFailureAt: now,
      lastLatencyMs: Math.max(0, Date.now() - startedAt),
      errorCode,
    });
  }

  snapshot(
    dependency: ExternalDependency,
    fallbackStatus: Exclude<ExternalDependencyStatus, 'ok' | 'error'>,
  ): ExternalDependencyHealth {
    const state = this.states.get(dependency);
    if (!state) {
      return {
        status: fallbackStatus,
        lastCheckedAt: null,
        lastSuccessAt: null,
        lastFailureAt: null,
        lastLatencyMs: null,
        errorCode: null,
      };
    }
    return {
      status: state.status,
      lastCheckedAt: state.lastCheckedAt.toISOString(),
      lastSuccessAt: state.lastSuccessAt?.toISOString() ?? null,
      lastFailureAt: state.lastFailureAt?.toISOString() ?? null,
      lastLatencyMs: state.lastLatencyMs,
      errorCode: state.errorCode,
    };
  }
}
