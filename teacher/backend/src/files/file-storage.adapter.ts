import type { Readable } from 'node:stream';

export type FileStorageProvider = 'LOCAL' | 'OSS';

export interface DirectUploadForm {
  uploadUrl: string;
  fields: Record<string, string>;
}

export interface StoredObjectMetadata {
  sizeBytes: number;
  mimeType: string;
  sha256: string | null;
}

export interface FileStorageBackend {
  readonly provider: FileStorageProvider;
  write(objectKey: string, content: Buffer): Promise<void>;
  writeStream?(objectKey: string, content: Readable): Promise<void>;
  read(objectKey: string): Promise<Buffer>;
  delete(objectKey: string): Promise<void>;
  createDirectUploadForm?(
    objectKey: string,
    input: {
      mimeType: string;
      sizeBytes: number;
      sha256: string;
      expiresAt: Date;
    },
  ): Promise<DirectUploadForm>;
  stat?(objectKey: string): Promise<StoredObjectMetadata>;
}

export abstract class FileStorageAdapter {
  abstract readonly activeProvider: FileStorageProvider;
  abstract write(
    objectKey: string,
    content: Buffer,
    provider?: FileStorageProvider,
  ): Promise<void>;
  abstract writeStream(
    objectKey: string,
    content: Readable,
    provider?: FileStorageProvider,
  ): Promise<void>;
  abstract read(
    objectKey: string,
    provider?: FileStorageProvider,
  ): Promise<Buffer>;
  abstract delete(
    objectKey: string,
    provider?: FileStorageProvider,
  ): Promise<void>;
  abstract createDirectUploadForm(
    objectKey: string,
    input: {
      mimeType: string;
      sizeBytes: number;
      sha256: string;
      expiresAt: Date;
    },
    provider?: FileStorageProvider,
  ): Promise<DirectUploadForm | null>;
  abstract stat(
    objectKey: string,
    provider?: FileStorageProvider,
  ): Promise<StoredObjectMetadata | null>;
}
