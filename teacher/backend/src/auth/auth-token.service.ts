import { Injectable } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { createHash, createHmac, randomBytes } from 'node:crypto';
import type { AppEnvironment } from '../platform/config/environment';

export interface RawAuthToken {
  raw: string;
  hash: string;
}

@Injectable()
export class AuthTokenService {
  constructor(private readonly config: ConfigService<AppEnvironment, true>) {}

  create(): RawAuthToken {
    const raw = randomBytes(32).toString('base64url');
    return { raw, hash: this.hash(raw) };
  }

  hash(raw: string): string {
    return createHash('sha256').update(raw).digest('hex');
  }

  hashPrivateValue(value: string): string {
    const secret = this.config.get('DATA_HASH_SECRET', { infer: true });

    if (secret) {
      return createHmac('sha256', secret).update(value).digest('hex');
    }

    return createHash('sha256')
      .update(`development-only:${value}`)
      .digest('hex');
  }
}
