import { Injectable } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { createWriteStream } from 'node:fs';
import { mkdir, readFile, rename, unlink, writeFile } from 'node:fs/promises';
import { dirname, isAbsolute, relative, resolve, sep } from 'node:path';
import { randomUUID } from 'node:crypto';
import type { Readable } from 'node:stream';
import { pipeline } from 'node:stream/promises';
import type { AppEnvironment } from '../platform/config/environment';
import type { FileStorageBackend } from './file-storage.adapter';

@Injectable()
export class LocalFileStorageAdapter implements FileStorageBackend {
  readonly provider = 'LOCAL' as const;
  private readonly root: string;

  constructor(config: ConfigService<AppEnvironment, true>) {
    this.root = resolve(config.get('LOCAL_FILE_STORAGE_DIR', { infer: true }));
  }

  async write(objectKey: string, content: Buffer): Promise<void> {
    const target = this.resolveObjectPath(objectKey);
    const temporary = `${target}.${randomUUID()}.uploading`;

    await mkdir(dirname(target), { recursive: true, mode: 0o700 });
    try {
      await writeFile(temporary, content, { flag: 'wx', mode: 0o600 });
      await rename(temporary, target);
    } catch (error) {
      await unlink(temporary).catch(() => undefined);
      throw error;
    }
  }

  async writeStream(objectKey: string, content: Readable): Promise<void> {
    const target = this.resolveObjectPath(objectKey);
    const temporary = `${target}.${randomUUID()}.uploading`;
    await mkdir(dirname(target), { recursive: true, mode: 0o700 });
    try {
      await pipeline(
        content,
        createWriteStream(temporary, { flags: 'wx', mode: 0o600 }),
      );
      await rename(temporary, target);
    } catch (error) {
      await unlink(temporary).catch(() => undefined);
      throw error;
    }
  }

  read(objectKey: string): Promise<Buffer> {
    return readFile(this.resolveObjectPath(objectKey));
  }

  async delete(objectKey: string): Promise<void> {
    try {
      await unlink(this.resolveObjectPath(objectKey));
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error;
    }
  }

  private resolveObjectPath(objectKey: string): string {
    if (isAbsolute(objectKey) || objectKey.includes('\0')) {
      throw new Error('Invalid storage object key');
    }

    const target = resolve(this.root, objectKey);
    const pathFromRoot = relative(this.root, target);
    if (
      pathFromRoot === '' ||
      pathFromRoot === '..' ||
      pathFromRoot.startsWith(`..${sep}`) ||
      isAbsolute(pathFromRoot)
    ) {
      throw new Error('Storage object key escaped its private root');
    }

    return target;
  }
}
