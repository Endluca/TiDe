import { ConfigService } from '@nestjs/config';
import { ConflictException } from '@nestjs/common';
import { createHash } from 'node:crypto';
import { Readable } from 'node:stream';
import type { AppEnvironment } from '../platform/config/environment';
import type { OwnedFileRecord } from './file.models';
import type { FileRepository } from './file.repository';
import { FileService } from './file.service';
import type { FileStorageAdapter } from './file-storage.adapter';

const principal = { accountId: 'account-001', sessionId: 'session-001' };
const content = Buffer.from('valid evidence');
const sha256 = createHash('sha256').update(content).digest('hex');

function createFixture(fileOverrides: Partial<OwnedFileRecord> = {}) {
  const file: OwnedFileRecord = {
    fileId: '11111111-1111-4111-8111-111111111111',
    status: 'PENDING',
    mimeType: 'image/png',
    sizeBytes: content.length,
    sha256,
    storageProvider: 'LOCAL',
    objectKey: 'uploads/account-001/file-id',
    originalFilename: 'proof.png',
    expiresAt: new Date(Date.now() + 60_000),
    uploadReceivedAt: null,
    ...fileOverrides,
  };
  const createIntent = jest.fn().mockResolvedValue({ type: 'CREATED', file });
  const findOwnedFile = jest.fn().mockResolvedValue(file);
  const markUploadReceived = jest.fn().mockResolvedValue(undefined);
  const markQuarantined = jest.fn().mockResolvedValue(undefined);
  const markReady = jest.fn().mockResolvedValue(undefined);
  const repository = {
    createIntent,
    findOwnedFile,
    markUploadReceived,
    markQuarantined,
    markReady,
  } as unknown as FileRepository;
  const write = jest.fn().mockResolvedValue(undefined);
  const writeStream = jest.fn(
    async (_objectKey: string, stream: Readable): Promise<void> => {
      for await (const chunk of stream) {
        // Consume the stream like the real storage adapter.
        void chunk;
      }
    },
  );
  const read = jest.fn().mockResolvedValue(content);
  const createDirectUploadForm = jest.fn().mockResolvedValue(null);
  const stat = jest.fn().mockResolvedValue(null);
  const deleteObject = jest.fn().mockResolvedValue(undefined);
  const storage = {
    write,
    writeStream,
    read,
    createDirectUploadForm,
    stat,
    delete: deleteObject,
  } as unknown as FileStorageAdapter;
  Object.assign(storage, { activeProvider: file.storageProvider });
  const config = new ConfigService<AppEnvironment, true>({
    PUBLIC_API_URL: 'http://localhost:3000',
    FILE_UPLOAD_MAX_BYTES: 10_000,
    FILE_UPLOAD_TTL_MINUTES: 15,
    FILE_ALLOWED_MIME_TYPES: 'image/png,application/pdf',
  });

  return {
    service: new FileService(repository, storage, config),
    createIntent,
    markUploadReceived,
    markQuarantined,
    markReady,
    write,
    writeStream,
    createDirectUploadForm,
    stat,
    read,
  };
}

describe('FileService', () => {
  it('creates a private local upload intent bound to a task step', async () => {
    const fixture = createFixture();

    const result = await fixture.service.createUploadIntent(
      principal,
      'intent-key-001',
      {
        taskInstanceId: '22222222-2222-4222-8222-222222222222',
        stepKey: 'proof',
        filename: '../proof.png',
        mimeType: 'image/png',
        sizeBytes: content.length,
        sha256,
      },
    );

    expect(result).toMatchObject({ uploadMethod: 'LOCAL_STREAM' });
    expect(result.uploadUrl).toContain('/api/v1/files/');
    expect(fixture.createIntent).toHaveBeenCalledWith(
      expect.objectContaining({
        originalFilename: 'proof.png',
        storageProvider: 'LOCAL',
      }),
    );
  });

  it('quarantines content when its digest does not match the intent', async () => {
    const fixture = createFixture();

    await expect(
      fixture.service.uploadStream(
        principal,
        '11111111-1111-4111-8111-111111111111',
        Readable.from(Buffer.from('tampered')),
        'image/png',
        String(content.length),
      ),
    ).rejects.toBeInstanceOf(ConflictException);
    expect(fixture.markQuarantined).toHaveBeenCalled();
    expect(fixture.writeStream).toHaveBeenCalled();
  });

  it('streams valid local content without buffering it in the service', async () => {
    const fixture = createFixture();

    await expect(
      fixture.service.uploadStream(
        principal,
        '11111111-1111-4111-8111-111111111111',
        Readable.from(content),
        'image/png',
        String(content.length),
      ),
    ).resolves.toMatchObject({ status: 'PENDING', sha256 });
    expect(fixture.markUploadReceived).toHaveBeenCalled();
  });

  it('creates and verifies a direct private OSS upload', async () => {
    const fixture = createFixture({ storageProvider: 'OSS' });
    fixture.createDirectUploadForm.mockResolvedValue({
      uploadUrl: 'https://bucket.example.com/',
      fields: { key: 'uploads/account-001/file-id' },
    });
    fixture.stat.mockResolvedValue({
      sizeBytes: content.length,
      mimeType: 'image/png',
      sha256,
    });

    const intent = await fixture.service.createUploadIntent(
      principal,
      'intent-key-oss-001',
      {
        taskInstanceId: '22222222-2222-4222-8222-222222222222',
        stepKey: 'proof',
        filename: 'proof.png',
        mimeType: 'image/png',
        sizeBytes: content.length,
        sha256,
      },
    );
    expect(intent).toMatchObject({
      uploadMethod: 'OSS_POST_FORM',
      requiredFields: { key: 'uploads/account-001/file-id' },
    });

    await expect(
      fixture.service.completeUpload(
        principal,
        '11111111-1111-4111-8111-111111111111',
        'complete-key-oss-001',
        { sha256 },
      ),
    ).resolves.toMatchObject({ status: 'READY' });
    expect(fixture.read).not.toHaveBeenCalled();
    expect(fixture.markUploadReceived).toHaveBeenCalled();
  });

  it('verifies stored content before marking a file ready', async () => {
    const fixture = createFixture({ uploadReceivedAt: new Date() });

    await expect(
      fixture.service.completeUpload(
        principal,
        '11111111-1111-4111-8111-111111111111',
        'complete-key-001',
        { sha256 },
      ),
    ).resolves.toMatchObject({ status: 'READY', sha256 });
    expect(fixture.markReady).toHaveBeenCalled();
  });
});
