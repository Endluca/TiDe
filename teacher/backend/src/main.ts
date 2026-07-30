import { ConfigService } from '@nestjs/config';
import { NestFactory } from '@nestjs/core';
import type { NestExpressApplication } from '@nestjs/platform-express';
import { Logger } from 'nestjs-pino';
import { AppModule } from './app.module';
import {
  type AppEnvironment,
  parseCorsOrigins,
} from './platform/config/environment';
import { configureTrustProxy } from './platform/http/trust-proxy';

async function bootstrap(): Promise<void> {
  const app = await NestFactory.create<NestExpressApplication>(AppModule);
  const config = app.get(ConfigService<AppEnvironment, true>);
  const port = config.get('PORT', { infer: true });
  const trustProxyHops = config.get('TRUST_PROXY_HOPS', { infer: true });

  app.useLogger(app.get(Logger));
  configureTrustProxy(app, trustProxyHops);
  app.enableCors({
    origin: parseCorsOrigins(config.get('CORS_ORIGINS', { infer: true })),
    credentials: false,
  });
  app.enableShutdownHooks();

  await app.listen(port, '0.0.0.0');
}

void bootstrap();
