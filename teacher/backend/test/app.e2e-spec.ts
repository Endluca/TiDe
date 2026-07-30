import { Test, type TestingModule } from '@nestjs/testing';
import type { INestApplication } from '@nestjs/common';
import type { Server } from 'node:http';
import request from 'supertest';
import { AppModule } from '../src/app.module';

describe('App (e2e)', () => {
  let app: INestApplication;

  beforeAll(async () => {
    const moduleFixture: TestingModule = await Test.createTestingModule({
      imports: [AppModule],
    }).compile();

    app = moduleFixture.createNestApplication();
    await app.init();
  });

  afterAll(async () => {
    await app.close();
  });

  it('GET /health', async () => {
    const server = app.getHttpServer() as Server;

    await request(server)
      .get('/health')
      .expect(200)
      .expect({ status: 'ok', service: 'tide-backend' });
  });

  it('returns the contract error shape with a request id', async () => {
    const server = app.getHttpServer() as Server;
    const response = await request(server)
      .get('/missing')
      .set('x-request-id', 'test-request-001')
      .expect(404);

    expect(response.headers['x-request-id']).toBe('test-request-001');
    expect(response.body).toEqual({
      code: 'HTTP_404',
      message: 'Cannot GET /missing',
      requestId: 'test-request-001',
      retryable: false,
    });
  });

  it('reports readiness only when both production data sources are configured', async () => {
    const server = app.getHttpServer() as Server;
    const expectedStatus =
      process.env.TIDE_DATABASE_URL && process.env.SHIWEN_READ_DATABASE_URL
        ? 200
        : 503;
    const response = await request(server)
      .get('/health/ready')
      .expect(expectedStatus);

    if (expectedStatus === 200) {
      expect(response.body).toMatchObject({
        status: 'ready',
        checks: { tide: 'ok', shiwenRead: 'ok' },
      });
    } else {
      expect(response.body).toMatchObject({
        code: 'SERVICE_NOT_READY',
        retryable: true,
        details: {
          tide: process.env.TIDE_DATABASE_URL ? 'ok' : 'not_configured',
          shiwenRead: process.env.SHIWEN_READ_DATABASE_URL
            ? 'ok'
            : 'not_configured',
        },
      });
    }
  });

  it('validates registration input before calling business services', async () => {
    const server = app.getHttpServer() as Server;
    const response = await request(server)
      .post('/api/v1/auth/register')
      .send({
        email: 'not-an-email',
        teacherId: '',
        password: 'short',
      })
      .expect(400);

    expect(response.body).toMatchObject({
      code: 'HTTP_400',
      retryable: false,
    });
  });
});
