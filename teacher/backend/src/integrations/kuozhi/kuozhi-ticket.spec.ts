import { buildPhpHttpQuery, createKuozhiTicketUrl } from './kuozhi-ticket';

describe('Kuozhi ticket signing', () => {
  it('matches PHP http_build_query RFC1738 encoding and key ordering', () => {
    expect(
      buildPhpHttpQuery({
        type: 'teacher',
        to: 'https://edu.51talk.com/course/520?noheader=1',
        id: 'T-001',
        appkey: 'test app~key',
      }),
    ).toBe(
      'appkey=test+app%7Ekey&id=T-001&to=https%3A%2F%2Fedu.51talk.com%2Fcourse%2F520%3Fnoheader%3D1&type=teacher',
    );
  });

  it('creates the expected HMAC-SHA256 Base64 ticket without exposing the secret', () => {
    const launchUrl = createKuozhiTicketUrl({
      loginUrl: 'https://edu.51suyang.cn/login/ticket',
      appKey: 'test app~key',
      secretKey: 'test-secret',
      teacherId: 'T-001',
      targetUrl: 'https://edu.51talk.com/course/520?noheader=1',
    });
    const url = new URL(launchUrl);

    expect(url.searchParams.get('alt')).toBe(
      'ZGMyNjgyYWM0NTNjNGIyMzFmMDBlODdhODJiYTg3OTNhNDlkZjMxMDZmOTAyODA5ZmFjYTQyNDhmODE2NzFmYw==',
    );
    expect(url.searchParams.get('to')).toBe(
      'https://edu.51talk.com/course/520?noheader=1',
    );
    expect(launchUrl).not.toContain('test-secret');
  });
});
