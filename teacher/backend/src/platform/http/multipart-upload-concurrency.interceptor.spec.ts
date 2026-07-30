import {
  type CallHandler,
  type ExecutionContext,
  HttpException,
} from '@nestjs/common';
import type { ConfigService } from '@nestjs/config';
import type { Request, Response } from 'express';
import { EventEmitter } from 'node:events';
import { of, Subject, throwError } from 'rxjs';
import type { AppEnvironment } from '../config/environment';
import { MultipartUploadConcurrencyInterceptor } from './multipart-upload-concurrency.interceptor';

interface HttpFixture {
  context: ExecutionContext;
  request: Request;
  response: Response;
  setHeader: jest.Mock;
}

function config(maxConcurrency: number): ConfigService<AppEnvironment, true> {
  return {
    get: jest.fn().mockReturnValue(maxConcurrency),
  } as unknown as ConfigService<AppEnvironment, true>;
}

function httpFixture(
  contentType = 'multipart/form-data; boundary=test',
): HttpFixture {
  const request = Object.assign(new EventEmitter(), {
    headers: { 'content-type': contentType },
  }) as unknown as Request;
  const setHeader = jest.fn();
  const response = Object.assign(new EventEmitter(), {
    setHeader,
  }) as unknown as Response;
  const context = {
    switchToHttp: () => ({
      getRequest: () => request,
      getResponse: () => response,
    }),
  } as ExecutionContext;
  return { context, request, response, setHeader };
}

describe('MultipartUploadConcurrencyInterceptor', () => {
  it('rejects overload before invoking the downstream Multer interceptor', () => {
    const interceptor = new MultipartUploadConcurrencyInterceptor(config(1));
    const first = httpFixture();
    const firstRequest = new Subject<unknown>();
    const firstHandle = jest.fn(() => firstRequest.asObservable());
    const firstNext = {
      handle: firstHandle,
    } as CallHandler;

    interceptor.intercept(first.context, firstNext).subscribe();

    const overloaded = httpFixture();
    const overloadedHandle = jest.fn(() => of(undefined));
    const overloadedNext = {
      handle: overloadedHandle,
    } as CallHandler;

    expect(() =>
      interceptor.intercept(overloaded.context, overloadedNext),
    ).toThrow(HttpException);
    expect(overloadedHandle).not.toHaveBeenCalled();
    expect(overloaded.setHeader).toHaveBeenCalledWith('Retry-After', '1');

    try {
      interceptor.intercept(overloaded.context, overloadedNext);
    } catch (error) {
      expect(error).toBeInstanceOf(HttpException);
      expect((error as HttpException).getStatus()).toBe(429);
      expect((error as HttpException).getResponse()).toMatchObject({
        code: 'MULTIPART_UPLOAD_CAPACITY_EXHAUSTED',
        retryable: true,
        details: { maxConcurrency: 1, retryAfterSeconds: 1 },
      });
    }

    firstRequest.complete();
  });

  it('releases capacity after completion and downstream failure', () => {
    const interceptor = new MultipartUploadConcurrencyInterceptor(config(1));

    const completed = httpFixture();
    interceptor
      .intercept(completed.context, { handle: () => of('done') })
      .subscribe();

    const failed = httpFixture();
    interceptor
      .intercept(failed.context, {
        handle: () => throwError(() => new Error('failed')),
      })
      .subscribe({ error: () => undefined });

    const next = httpFixture();
    const nextHandle = jest.fn(() => of('accepted'));
    expect(() =>
      interceptor.intercept(next.context, { handle: nextHandle }).subscribe(),
    ).not.toThrow();
    expect(nextHandle).toHaveBeenCalledTimes(1);
  });

  it('does not release an in-flight slot before the downstream upload ends', () => {
    const interceptor = new MultipartUploadConcurrencyInterceptor(config(1));
    const active = httpFixture();
    const pending = new Subject<unknown>();
    interceptor
      .intercept(active.context, { handle: () => pending.asObservable() })
      .subscribe();
    active.request.emit('aborted');
    active.response.emit('close');

    const overloaded = httpFixture();
    const overloadedHandle = jest.fn(() => of(undefined));
    expect(() =>
      interceptor.intercept(overloaded.context, {
        handle: overloadedHandle,
      }),
    ).toThrow(HttpException);
    expect(overloadedHandle).not.toHaveBeenCalled();

    pending.complete();
    expect(() =>
      interceptor
        .intercept(overloaded.context, { handle: overloadedHandle })
        .subscribe(),
    ).not.toThrow();
  });

  it('does not consume capacity for non-multipart requests', () => {
    const interceptor = new MultipartUploadConcurrencyInterceptor(config(1));
    const fixture = httpFixture('application/json');
    const handle = jest.fn(() => of('ok'));

    interceptor.intercept(fixture.context, { handle }).subscribe();

    expect(handle).toHaveBeenCalledTimes(1);
    expect(fixture.setHeader).not.toHaveBeenCalled();
  });
});
