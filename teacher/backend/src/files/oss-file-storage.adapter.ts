import { Injectable } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import OSS from 'ali-oss';
import type { Readable } from 'node:stream';
import type { AppEnvironment } from '../platform/config/environment';
import type {
  DirectUploadForm,
  FileStorageBackend,
  StoredObjectMetadata,
} from './file-storage.adapter';

@Injectable()
export class OssFileStorageAdapter implements FileStorageBackend {
  readonly provider = 'OSS' as const;
  private client: OSS | null = null;

  constructor(private readonly config: ConfigService<AppEnvironment, true>) {}

  async write(objectKey: string, content: Buffer): Promise<void> {
    this.assertObjectKey(objectKey);
    await this.getClient().put(objectKey, content, {
      headers: {
        'x-oss-object-acl': 'private',
      },
    });
  }

  async writeStream(objectKey: string, content: Readable): Promise<void> {
    this.assertObjectKey(objectKey);
    await this.getClient().putStream(objectKey, content, {
      headers: {
        'x-oss-object-acl': 'private',
      },
    } as OSS.PutStreamOptions);
  }

  async read(objectKey: string): Promise<Buffer> {
    this.assertObjectKey(objectKey);
    const result = await this.getClient().get(objectKey);
    return Buffer.isBuffer(result.content)
      ? result.content
      : Buffer.from(result.content);
  }

  async delete(objectKey: string): Promise<void> {
    this.assertObjectKey(objectKey);
    await this.getClient().delete(objectKey);
  }

  createDirectUploadForm(
    objectKey: string,
    input: {
      mimeType: string;
      sizeBytes: number;
      sha256: string;
      expiresAt: Date;
    },
  ): Promise<DirectUploadForm> {
    this.assertObjectKey(objectKey);
    const policy = {
      expiration: input.expiresAt.toISOString(),
      conditions: [
        ['eq', '$key', objectKey],
        ['eq', '$Content-Type', input.mimeType],
        ['eq', '$x-oss-object-acl', 'private'],
        ['eq', '$x-oss-meta-sha256', input.sha256],
        ['content-length-range', input.sizeBytes, input.sizeBytes],
      ],
    };
    const signature = this.getClient().calculatePostSignature(policy);
    return Promise.resolve({
      uploadUrl: this.bucketUrl(),
      fields: {
        key: objectKey,
        policy: signature.policy,
        OSSAccessKeyId: signature.OSSAccessKeyId,
        Signature: signature.Signature,
        'Content-Type': input.mimeType,
        'x-oss-object-acl': 'private',
        'x-oss-meta-sha256': input.sha256,
      },
    });
  }

  async stat(objectKey: string): Promise<StoredObjectMetadata> {
    this.assertObjectKey(objectKey);
    const result = await this.getClient().head(objectKey);
    const headers = result.res.headers as Record<string, unknown>;
    const meta = result.meta as Record<string, unknown>;
    const contentType = headers['content-type'];
    return {
      sizeBytes: Number(headers['content-length'] ?? 0),
      mimeType:
        typeof contentType === 'string' ? contentType.toLowerCase() : '',
      sha256:
        typeof meta.sha256 === 'string'
          ? meta.sha256
          : typeof headers['x-oss-meta-sha256'] === 'string'
            ? headers['x-oss-meta-sha256']
            : null,
    };
  }

  private getClient(): OSS {
    if (this.client) return this.client;

    const region = this.config.get('OSS_REGION', { infer: true });
    const endpoint = this.config.get('OSS_ENDPOINT', { infer: true });
    const bucket = this.config.get('OSS_BUCKET', { infer: true });
    const accessKeyId = this.config.get('OSS_ACCESS_KEY_ID', { infer: true });
    const accessKeySecret = this.config.get('OSS_ACCESS_KEY_SECRET', {
      infer: true,
    });
    if (!region || !endpoint || !bucket || !accessKeyId || !accessKeySecret) {
      throw new Error('OSS storage credentials are not configured');
    }

    this.client = new OSS({
      region,
      endpoint: this.normalizeEndpoint(endpoint, bucket),
      bucket,
      accessKeyId,
      accessKeySecret,
      authorizationV4: true,
      timeout: this.config.get('OSS_TIMEOUT_MS', { infer: true }),
    });
    return this.client;
  }

  private normalizeEndpoint(endpoint: string, bucket: string): string {
    const url = new URL(endpoint);
    const bucketPrefix = `${bucket}.`;
    if (
      url.hostname.startsWith(bucketPrefix) &&
      url.hostname.slice(bucketPrefix.length).startsWith('oss-') &&
      url.hostname.endsWith('.aliyuncs.com')
    ) {
      url.hostname = url.hostname.slice(bucketPrefix.length);
    }
    return url.toString().replace(/\/$/, '');
  }

  private bucketUrl(): string {
    const bucket = this.config.get('OSS_BUCKET', { infer: true });
    const endpoint = this.config.get('OSS_ENDPOINT', { infer: true });
    if (!bucket || !endpoint) {
      throw new Error('OSS storage credentials are not configured');
    }
    const url = new URL(this.normalizeEndpoint(endpoint, bucket));
    if (!url.hostname.startsWith(`${bucket}.`)) {
      url.hostname = `${bucket}.${url.hostname}`;
    }
    url.pathname = '/';
    return url.toString();
  }

  private assertObjectKey(objectKey: string): void {
    if (
      !objectKey ||
      objectKey.startsWith('/') ||
      objectKey.includes('\0') ||
      objectKey.split('/').some((segment) => segment === '..')
    ) {
      throw new Error('Invalid storage object key');
    }
  }
}
