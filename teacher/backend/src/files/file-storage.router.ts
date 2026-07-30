import { Injectable, Optional } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import type { Readable } from 'node:stream';
import type { AppEnvironment } from '../platform/config/environment';
import { DependencyHealthRegistry } from '../platform/observability/dependency-health.registry';
import {
  FileStorageAdapter,
  type DirectUploadForm,
  type FileStorageBackend,
  type FileStorageProvider,
  type StoredObjectMetadata,
} from './file-storage.adapter';
import { LocalFileStorageAdapter } from './local-file-storage.adapter';
import { OssFileStorageAdapter } from './oss-file-storage.adapter';

@Injectable()
export class FileStorageRouter extends FileStorageAdapter {
  readonly activeProvider: FileStorageProvider;
  private readonly backends: Map<FileStorageProvider, FileStorageBackend>;

  constructor(
    local: LocalFileStorageAdapter,
    oss: OssFileStorageAdapter,
    config: ConfigService<AppEnvironment, true>,
    @Optional() private readonly dependencyHealth?: DependencyHealthRegistry,
  ) {
    super();
    this.activeProvider = config.get('FILE_STORAGE_PROVIDER', { infer: true });
    this.backends = new Map<FileStorageProvider, FileStorageBackend>([
      [local.provider, local],
      [oss.provider, oss],
    ]);
  }

  async write(
    objectKey: string,
    content: Buffer,
    provider: FileStorageProvider = this.activeProvider,
  ): Promise<void> {
    const startedAt = Date.now();
    try {
      await this.backend(provider).write(objectKey, content);
      this.dependencyHealth?.recordSuccess('fileStorage', startedAt);
    } catch (error) {
      this.dependencyHealth?.recordFailure(
        'fileStorage',
        error instanceof Error ? error.name : 'FILE_STORAGE_ERROR',
        startedAt,
      );
      throw error;
    }
  }

  async writeStream(
    objectKey: string,
    content: Readable,
    provider: FileStorageProvider = this.activeProvider,
  ): Promise<void> {
    const backend = this.backend(provider);
    if (!backend.writeStream) {
      throw new Error(`Streaming upload is unsupported: ${provider}`);
    }
    const startedAt = Date.now();
    try {
      await backend.writeStream(objectKey, content);
      this.dependencyHealth?.recordSuccess('fileStorage', startedAt);
    } catch (error) {
      this.dependencyHealth?.recordFailure(
        'fileStorage',
        error instanceof Error ? error.name : 'FILE_STORAGE_ERROR',
        startedAt,
      );
      throw error;
    }
  }

  async read(
    objectKey: string,
    provider: FileStorageProvider = this.activeProvider,
  ): Promise<Buffer> {
    const startedAt = Date.now();
    try {
      const content = await this.backend(provider).read(objectKey);
      this.dependencyHealth?.recordSuccess('fileStorage', startedAt);
      return content;
    } catch (error) {
      this.dependencyHealth?.recordFailure(
        'fileStorage',
        error instanceof Error ? error.name : 'FILE_STORAGE_ERROR',
        startedAt,
      );
      throw error;
    }
  }

  async delete(
    objectKey: string,
    provider: FileStorageProvider = this.activeProvider,
  ): Promise<void> {
    const startedAt = Date.now();
    try {
      await this.backend(provider).delete(objectKey);
      this.dependencyHealth?.recordSuccess('fileStorage', startedAt);
    } catch (error) {
      this.dependencyHealth?.recordFailure(
        'fileStorage',
        error instanceof Error ? error.name : 'FILE_STORAGE_ERROR',
        startedAt,
      );
      throw error;
    }
  }

  createDirectUploadForm(
    objectKey: string,
    input: {
      mimeType: string;
      sizeBytes: number;
      sha256: string;
      expiresAt: Date;
    },
    provider: FileStorageProvider = this.activeProvider,
  ): Promise<DirectUploadForm | null> {
    const backend = this.backend(provider);
    return backend.createDirectUploadForm
      ? backend.createDirectUploadForm(objectKey, input)
      : Promise.resolve(null);
  }

  stat(
    objectKey: string,
    provider: FileStorageProvider = this.activeProvider,
  ): Promise<StoredObjectMetadata | null> {
    const backend = this.backend(provider);
    return backend.stat ? backend.stat(objectKey) : Promise.resolve(null);
  }

  private backend(provider: FileStorageProvider): FileStorageBackend {
    const backend = this.backends.get(provider);
    if (!backend) throw new Error(`Unsupported storage provider: ${provider}`);
    return backend;
  }
}
