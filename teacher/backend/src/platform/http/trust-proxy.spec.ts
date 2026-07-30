import express from 'express';
import request from 'supertest';
import { configureTrustProxy } from './trust-proxy';

describe('configureTrustProxy', () => {
  it('uses the canonical client address supplied by the sanitizing Edge', async () => {
    const app = express();
    configureTrustProxy(app, 1);
    app.get('/ip', (incoming, response) => {
      response.json({ ip: incoming.ip, ips: incoming.ips });
    });

    const response = await request(app)
      .get('/ip')
      .set('X-Forwarded-For', '203.0.113.17')
      .expect(200);

    expect(response.body).toEqual({
      ip: '203.0.113.17',
      ips: ['203.0.113.17'],
    });
  });

  it('does not trust a forwarded address when the hop count is zero', async () => {
    const app = express();
    configureTrustProxy(app, 0);
    app.get('/ip', (incoming, response) => {
      response.json({ ip: incoming.ip });
    });

    const response = await request(app)
      .get('/ip')
      .set('X-Forwarded-For', '203.0.113.17, 10.20.30.40')
      .expect(200);

    expect(response.body).not.toEqual({ ip: '203.0.113.17' });
  });
});
