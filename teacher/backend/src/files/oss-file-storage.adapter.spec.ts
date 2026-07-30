import { ConfigService } from '@nestjs/config';
import OSS from 'ali-oss';
import { Readable } from 'node:stream';
import type { AppEnvironment } from '../platform/config/environment';
import { OssFileStorageAdapter } from './oss-file-storage.adapter';

const mockPut = jest.fn();
const mockGet = jest.fn();
const mockDelete = jest.fn();
const mockPutStream = jest.fn();
const mockHead = jest.fn();
const mockCalculatePostSignature = jest.fn();

jest.mock('ali-oss', () => ({
  __esModule: true,
  default: jest.fn().mockImplementation(() => ({
    put: mockPut,
    putStream: mockPutStream,
    get: mockGet,
    delete: mockDelete,
    head: mockHead,
    calculatePostSignature: mockCalculatePostSignature,
  })),
}));

describe('OssFileStorageAdapter', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockPut.mockResolvedValue({});
    mockGet.mockResolvedValue({ content: Buffer.from('evidence') });
    mockDelete.mockResolvedValue({});
    mockPutStream.mockResolvedValue({});
    mockHead.mockResolvedValue({
      res: {
        headers: {
          'content-length': '8',
          'content-type': 'image/png',
          'x-oss-meta-sha256': 'abc123',
        },
      },
      meta: { sha256: 'abc123' },
    });
    mockCalculatePostSignature.mockReturnValue({
      policy: 'encoded-policy',
      OSSAccessKeyId: 'test-access-key-id',
      Signature: 'signed-policy',
    });
  });

  it('creates an exact private browser upload policy and verifies metadata', async () => {
    const config = new ConfigService<AppEnvironment, true>({
      OSS_REGION: 'oss-ap-southeast-1',
      OSS_ENDPOINT: 'https://oss-ap-southeast-1.aliyuncs.com',
      OSS_BUCKET: 'eff-new-teacher-camp',
      OSS_ACCESS_KEY_ID: 'test-access-key-id',
      OSS_ACCESS_KEY_SECRET: 'test-access-key-secret',
      OSS_TIMEOUT_MS: 30_000,
    });
    const storage = new OssFileStorageAdapter(config);
    const expiresAt = new Date(Date.now() + 60_000);

    await expect(
      storage.createDirectUploadForm('uploads/account/file-id', {
        mimeType: 'image/png',
        sizeBytes: 8,
        sha256: 'abc123',
        expiresAt,
      }),
    ).resolves.toMatchObject({
      uploadUrl:
        'https://eff-new-teacher-camp.oss-ap-southeast-1.aliyuncs.com/',
      fields: {
        key: 'uploads/account/file-id',
        'Content-Type': 'image/png',
        'x-oss-object-acl': 'private',
        'x-oss-meta-sha256': 'abc123',
      },
    });
    expect(mockCalculatePostSignature).toHaveBeenCalledWith(
      expect.objectContaining({
        expiration: expiresAt.toISOString(),
      }),
    );
    expect(JSON.stringify(mockCalculatePostSignature.mock.calls[0])).toContain(
      '["content-length-range",8,8]',
    );
    expect(JSON.stringify(mockCalculatePostSignature.mock.calls[0])).toContain(
      '["eq","$x-oss-object-acl","private"]',
    );
    await expect(storage.stat('uploads/account/file-id')).resolves.toEqual({
      sizeBytes: 8,
      mimeType: 'image/png',
      sha256: 'abc123',
    });
  });

  it('streams fallback content to private OSS objects', async () => {
    const storage = new OssFileStorageAdapter(
      new ConfigService<AppEnvironment, true>({
        OSS_REGION: 'oss-ap-southeast-1',
        OSS_ENDPOINT: 'https://oss-ap-southeast-1.aliyuncs.com',
        OSS_BUCKET: 'eff-new-teacher-camp',
        OSS_ACCESS_KEY_ID: 'test-access-key-id',
        OSS_ACCESS_KEY_SECRET: 'test-access-key-secret',
        OSS_TIMEOUT_MS: 30_000,
      }),
    );
    const stream = Readable.from(Buffer.from('evidence'));

    await storage.writeStream('uploads/account/file-id', stream);

    expect(mockPutStream).toHaveBeenCalledWith(
      'uploads/account/file-id',
      stream,
      { headers: { 'x-oss-object-acl': 'private' } },
    );
  });

  it('uses Signature V4 and keeps uploaded objects private', async () => {
    const config = new ConfigService<AppEnvironment, true>({
      OSS_REGION: 'oss-ap-southeast-1',
      OSS_ENDPOINT:
        'https://eff-new-teacher-camp.oss-ap-southeast-1.aliyuncs.com',
      OSS_BUCKET: 'eff-new-teacher-camp',
      OSS_ACCESS_KEY_ID: 'test-access-key-id',
      OSS_ACCESS_KEY_SECRET: 'test-access-key-secret',
      OSS_TIMEOUT_MS: 30_000,
    });
    const storage = new OssFileStorageAdapter(config);

    await storage.write('uploads/account/file-id', Buffer.from('evidence'));
    await expect(storage.read('uploads/account/file-id')).resolves.toEqual(
      Buffer.from('evidence'),
    );

    expect(OSS).toHaveBeenCalledWith(
      expect.objectContaining({
        region: 'oss-ap-southeast-1',
        endpoint: 'https://oss-ap-southeast-1.aliyuncs.com',
        bucket: 'eff-new-teacher-camp',
        authorizationV4: true,
      }),
    );
    expect(mockPut).toHaveBeenCalledWith(
      'uploads/account/file-id',
      Buffer.from('evidence'),
      { headers: { 'x-oss-object-acl': 'private' } },
    );
  });

  it('deletes the requested private object', async () => {
    const storage = new OssFileStorageAdapter(
      new ConfigService<AppEnvironment, true>({
        OSS_REGION: 'oss-ap-southeast-1',
        OSS_ENDPOINT: 'https://oss-ap-southeast-1.aliyuncs.com',
        OSS_BUCKET: 'eff-new-teacher-camp',
        OSS_ACCESS_KEY_ID: 'test-access-key-id',
        OSS_ACCESS_KEY_SECRET: 'test-access-key-secret',
        OSS_TIMEOUT_MS: 30_000,
      }),
    );

    await storage.delete('support-tickets/ticket/message/image.png');

    expect(mockDelete).toHaveBeenCalledWith(
      'support-tickets/ticket/message/image.png',
    );
  });

  it('rejects unsafe object keys before calling OSS', async () => {
    const storage = new OssFileStorageAdapter(
      new ConfigService<AppEnvironment, true>({}),
    );

    await expect(storage.write('../outside', Buffer.from('x'))).rejects.toThrow(
      'Invalid',
    );
    expect(OSS).not.toHaveBeenCalled();
  });
});
