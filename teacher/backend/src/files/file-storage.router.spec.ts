import { ConfigService } from '@nestjs/config';
import type { AppEnvironment } from '../platform/config/environment';
import { FileStorageRouter } from './file-storage.router';
import type { LocalFileStorageAdapter } from './local-file-storage.adapter';
import type { OssFileStorageAdapter } from './oss-file-storage.adapter';

describe('FileStorageRouter', () => {
  it('writes to the configured provider and can still read old local files', async () => {
    const localRead = jest.fn().mockResolvedValue(Buffer.from('local'));
    const localDelete = jest.fn().mockResolvedValue(undefined);
    const ossWrite = jest.fn().mockResolvedValue(undefined);
    const local = {
      provider: 'LOCAL',
      write: jest.fn().mockResolvedValue(undefined),
      read: localRead,
      delete: localDelete,
    } as unknown as LocalFileStorageAdapter;
    const oss = {
      provider: 'OSS',
      write: ossWrite,
      read: jest.fn().mockResolvedValue(Buffer.from('oss')),
      delete: jest.fn().mockResolvedValue(undefined),
    } as unknown as OssFileStorageAdapter;
    const config = new ConfigService<AppEnvironment, true>({
      FILE_STORAGE_PROVIDER: 'OSS',
    });
    const router = new FileStorageRouter(local, oss, config);

    await router.write('uploads/new-file', Buffer.from('new'));
    await expect(router.read('uploads/old-file', 'LOCAL')).resolves.toEqual(
      Buffer.from('local'),
    );
    await expect(
      router.delete('uploads/old-file', 'LOCAL'),
    ).resolves.toBeUndefined();

    expect(ossWrite).toHaveBeenCalledWith(
      'uploads/new-file',
      Buffer.from('new'),
    );
    expect(localRead).toHaveBeenCalledWith('uploads/old-file');
    expect(localDelete).toHaveBeenCalledWith('uploads/old-file');
  });
});
