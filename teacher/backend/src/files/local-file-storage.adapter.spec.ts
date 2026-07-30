import { ConfigService } from '@nestjs/config';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import type { AppEnvironment } from '../platform/config/environment';
import { LocalFileStorageAdapter } from './local-file-storage.adapter';

describe('LocalFileStorageAdapter', () => {
  let storageRoot: string;
  let storage: LocalFileStorageAdapter;

  beforeEach(async () => {
    storageRoot = await mkdtemp(join(tmpdir(), 'tide-storage-test-'));
    const config = new ConfigService<AppEnvironment, true>({
      LOCAL_FILE_STORAGE_DIR: storageRoot,
    });
    storage = new LocalFileStorageAdapter(config);
  });

  afterEach(async () => {
    await rm(storageRoot, { recursive: true, force: true });
  });

  it('writes and reads a private object', async () => {
    await storage.write('uploads/account/file-id', Buffer.from('evidence'));

    await expect(storage.read('uploads/account/file-id')).resolves.toEqual(
      Buffer.from('evidence'),
    );
  });

  it('deletes an object idempotently', async () => {
    await storage.write('uploads/account/file-id', Buffer.from('evidence'));

    await expect(
      storage.delete('uploads/account/file-id'),
    ).resolves.toBeUndefined();
    await expect(storage.read('uploads/account/file-id')).rejects.toMatchObject(
      {
        code: 'ENOENT',
      },
    );
    await expect(
      storage.delete('uploads/account/file-id'),
    ).resolves.toBeUndefined();
  });

  it('rejects keys that escape the storage root', async () => {
    await expect(storage.write('../outside', Buffer.from('x'))).rejects.toThrow(
      'escaped',
    );
  });
});
