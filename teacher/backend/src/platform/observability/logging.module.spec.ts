import type { IncomingMessage, ServerResponse } from 'node:http';
import { requestLogLevel, sanitizeRequestUrl } from './logging.module';

describe('requestLogLevel', () => {
  afterEach(() => jest.restoreAllMocks());

  it('keeps errors while sampling high-frequency successes', () => {
    const request = {
      url: '/api/v1/app-events/batch',
    } as IncomingMessage;
    const response = { statusCode: 202 } as ServerResponse;
    jest.spyOn(Math, 'random').mockReturnValue(0.5);

    expect(requestLogLevel(request, response)).toBe('silent');
    response.statusCode = 500;
    expect(requestLogLevel(request, response)).toBe('error');
  });

  it('removes credentials from request query strings before logging', () => {
    expect(
      sanitizeRequestUrl('/api/v1/auth/crm-sso?redirect=%2F&token=secret-jwt'),
    ).toBe('/api/v1/auth/crm-sso');
  });
});
