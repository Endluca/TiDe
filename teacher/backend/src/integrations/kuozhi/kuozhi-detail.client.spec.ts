import { ConfigService } from '@nestjs/config';
import { createServer } from 'node:http';
import type { AddressInfo } from 'node:net';
import type { AppEnvironment } from '../../platform/config/environment';
import { KuozhiDetailClient } from './kuozhi-detail.client';

describe('KuozhiDetailClient', () => {
  afterEach(() => jest.restoreAllMocks());

  it('sends the required media type and query parameters', async () => {
    const fetchMock = jest.spyOn(global, 'fetch').mockResolvedValue(
      new Response(
        JSON.stringify({
          id: '513',
          title: 'Teacher Tie-Up Program',
          task_list: null,
        }),
        { status: 200, headers: { 'content-type': 'application/json' } },
      ),
    );
    const values: Partial<AppEnvironment> = {
      KUOZHI_DETAIL_URL: 'http://edu.51talk.me/api/me/TeacherCourseDetail',
      KUOZHI_DETAIL_TIMEOUT_MS: 8_000,
      KUOZHI_DETAIL_RETRY_COUNT: 1,
    };
    const client = new KuozhiDetailClient({
      get: (key: keyof AppEnvironment) => values[key],
    } as ConfigService<AppEnvironment, true>);

    await expect(client.getCourseDetail('TEACHER-001', '513')).resolves.toEqual(
      expect.objectContaining({ id: '513' }),
    );
    const [url, options] = fetchMock.mock.calls[0];
    const requestUrl =
      typeof url === 'string' ? url : url instanceof URL ? url.href : url.url;
    expect(requestUrl).toContain('course_id=513');
    expect(requestUrl).toContain('t_id=TEACHER-001');
    expect(options?.headers).toEqual({
      Accept: 'application/vnd.edusoho.v2+json',
    });
  });

  it('resolves the detail hostname to the backend-only host override', async () => {
    let requestHost = '';
    let requestUrl = '';
    let requestAccept = '';
    const server = createServer((request, response) => {
      requestHost = request.headers.host ?? '';
      requestUrl = request.url ?? '';
      requestAccept = request.headers.accept ?? '';
      response.writeHead(200, { 'content-type': 'application/json' });
      response.end(
        JSON.stringify({
          id: '513',
          title: 'Teacher Tie-Up Program',
          task_list: null,
        }),
      );
    });
    await new Promise<void>((resolve) =>
      server.listen(0, '127.0.0.1', resolve),
    );
    const address = server.address() as AddressInfo;
    const values: Partial<AppEnvironment> = {
      KUOZHI_DETAIL_URL: `http://edu.51talk.me:${address.port}/api/me/TeacherCourseDetail`,
      KUOZHI_DETAIL_HOST_IP: '127.0.0.1',
      KUOZHI_DETAIL_TIMEOUT_MS: 8_000,
      KUOZHI_DETAIL_RETRY_COUNT: 0,
    };
    const client = new KuozhiDetailClient({
      get: (key: keyof AppEnvironment) => values[key],
    } as ConfigService<AppEnvironment, true>);

    try {
      await expect(
        client.getCourseDetail('TEACHER-001', '513'),
      ).resolves.toEqual(expect.objectContaining({ id: '513' }));
      expect(requestHost).toBe(`edu.51talk.me:${address.port}`);
      expect(requestUrl).toContain('course_id=513');
      expect(requestUrl).toContain('t_id=TEACHER-001');
      expect(requestAccept).toBe('application/vnd.edusoho.v2+json');
    } finally {
      await new Promise<void>((resolve, reject) =>
        server.close((error) => (error ? reject(error) : resolve())),
      );
    }
  });
});
