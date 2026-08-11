import { Injectable } from '@nestjs/common';
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const G02_CONTENT_VERSION = '2026-07-24-overseas-nt-policies-v1';
const G02_CONTENT_HASH =
  '6875233667c6f3d90602a07c84849dbb88f41685929779a7b0dc79ccf859979c';
const G02_TRANSLATION_HASH =
  '46f526933d681b18118582364dcf203b2ba2c93d86d4f51ab468725d0a33a543';
const G02_IMAGE_HASH =
  '960dfdde25ba3b4b7362694714cd498f069482bfa4b09a97df8f3cc06a829fc9';
const G02_IMAGE_SOURCE =
  '/core/api/resources/img/5eecdaf48460cde5986ec04012a93ff33f0191d60d929d9e75b8339e1c4c2483621fc1e46d5affd58a3a5b4275315d43a156a98577f418d5c7ae4899c7d4a3a87fbd89d8848cf1a0fefaea1882610f6dbea6c0a0bd3c816291dbda664cf7fee3';

interface DocumentStepConfig {
  documentCode?: unknown;
  sourceTitle?: unknown;
  sourceUpdatedAt?: unknown;
  contentVersion?: unknown;
  contentHash?: unknown;
  readingCompletion?: unknown;
}

export interface TaskDocumentContentResponse {
  documentCode: string;
  title: string;
  sourceUpdatedAt: string;
  contentVersion: string;
  contentHash: string;
  completionMode: 'SCROLL_TO_END';
  markdown: { en: string; zh: string };
  translationHashes: { zh: string };
  images: Array<{
    key: string;
    sourcePath: string;
    sha256: string;
    alt: string;
  }>;
}

export interface TaskDocumentAsset {
  bytes: Buffer;
  contentType: string;
  sha256: string;
}

export class TaskDocumentContentUnavailableError extends Error {
  constructor() {
    super('The configured document content is unavailable');
    this.name = 'TaskDocumentContentUnavailableError';
  }
}

@Injectable()
export class TaskDocumentContentService {
  private contentCache: TaskDocumentContentResponse | null = null;
  private imageCache: Buffer | null = null;

  getContent(config: DocumentStepConfig): TaskDocumentContentResponse {
    try {
      this.assertCurrentConfig(config);
      if (!this.contentCache) {
        const contentDirectory = this.contentDirectory();
        const english = this.readVerifiedText(
          resolve(contentDirectory, 'en.md'),
          G02_CONTENT_HASH,
        );
        const chinese = this.readVerifiedText(
          resolve(contentDirectory, 'zh.md'),
          G02_TRANSLATION_HASH,
        );
        this.contentCache = {
          documentCode: 'overseas-nt-policies',
          title: 'Overseas NT Policies',
          sourceUpdatedAt: '2026-07-24T01:47:08Z',
          contentVersion: G02_CONTENT_VERSION,
          contentHash: G02_CONTENT_HASH,
          completionMode: 'SCROLL_TO_END',
          markdown: { en: english, zh: chinese },
          translationHashes: { zh: G02_TRANSLATION_HASH },
          images: [
            {
              key: 'updated-unlocking-process',
              sourcePath: G02_IMAGE_SOURCE,
              sha256: G02_IMAGE_HASH,
              alt: 'Updated unlocking process',
            },
          ],
        };
      }
      return this.contentCache;
    } catch (error) {
      if (error instanceof TaskDocumentContentUnavailableError) throw error;
      throw new TaskDocumentContentUnavailableError();
    }
  }

  getAsset(config: DocumentStepConfig, assetKey: string): TaskDocumentAsset {
    try {
      this.assertCurrentConfig(config);
      if (assetKey !== 'updated-unlocking-process') {
        throw new TaskDocumentContentUnavailableError();
      }
      if (!this.imageCache) {
        const bytes = readFileSync(
          resolve(this.contentDirectory(), 'updated-unlocking-process.jpg'),
        );
        if (this.sha256(bytes) !== G02_IMAGE_HASH) {
          throw new TaskDocumentContentUnavailableError();
        }
        this.imageCache = bytes;
      }
      return {
        bytes: this.imageCache,
        contentType: 'image/jpeg',
        sha256: G02_IMAGE_HASH,
      };
    } catch (error) {
      if (error instanceof TaskDocumentContentUnavailableError) throw error;
      throw new TaskDocumentContentUnavailableError();
    }
  }

  private assertCurrentConfig(config: DocumentStepConfig): void {
    if (
      config.documentCode !== 'overseas-nt-policies' ||
      config.contentVersion !== G02_CONTENT_VERSION ||
      config.contentHash !== G02_CONTENT_HASH ||
      config.readingCompletion !== 'SCROLL_TO_END'
    ) {
      throw new TaskDocumentContentUnavailableError();
    }
  }

  private contentDirectory(): string {
    return resolve(process.cwd(), 'content', 'g02', G02_CONTENT_VERSION);
  }

  private readVerifiedText(path: string, expectedHash: string): string {
    const bytes = readFileSync(path);
    if (this.sha256(bytes) !== expectedHash) {
      throw new TaskDocumentContentUnavailableError();
    }
    return bytes.toString('utf8');
  }

  private sha256(value: Buffer): string {
    return createHash('sha256').update(value).digest('hex');
  }
}
