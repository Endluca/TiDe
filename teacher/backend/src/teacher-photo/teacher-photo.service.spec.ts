import { BadRequestException } from '@nestjs/common';
import type { ConfigService } from '@nestjs/config';
import sharp from 'sharp';
import type { AuthPrincipal } from '../auth/auth.models';
import type { FileStorageAdapter } from '../files/file-storage.adapter';
import type { AiGatewayService } from '../integrations/ai/ai-gateway.service';
import type { AppEnvironment } from '../platform/config/environment';
import type { CreamBright04Processor } from './cream-bright-04.processor';
import type { TeacherPhotoRunRecord } from './teacher-photo.models';
import type { TeacherPhotoRepository } from './teacher-photo.repository';
import {
  TEACHER_PHOTO_MIN_CONFIDENCE,
  TeacherPhotoService,
} from './teacher-photo.service';

const principal: AuthPrincipal = {
  accountId: 'account-001',
  sessionId: 'session-001',
};

function run(
  overrides: Partial<TeacherPhotoRunRecord> = {},
): TeacherPhotoRunRecord {
  return {
    photoRunId: 'photo-run-001',
    taskInstanceId: 'assignment-001',
    accountId: principal.accountId,
    originalFileId: 'original-file-001',
    originalStorageProvider: 'LOCAL',
    originalObjectKey: 'teacher-photos/account-001/original',
    originalFilename: 'camera.jpg',
    originalMimeType: 'image/jpeg',
    finalFileId: null,
    finalStorageProvider: null,
    finalObjectKey: null,
    status: 'CHECKING',
    decision: null,
    criteriaVersion: 'criteria-v1',
    teacherMessage: null,
    checks: [],
    filterPreset: null,
    filterStrength: null,
    submittedAt: new Date('2026-07-27T08:00:00Z'),
    checkedAt: null,
    processedAt: null,
    processingOwner: 'worker-001',
    attemptCount: 1,
    ...overrides,
  };
}

function fixture() {
  const repository = {
    reserve: jest.fn(),
    markOriginalReady: jest.fn().mockResolvedValue(undefined),
    markUploadFailed: jest.fn().mockResolvedValue(undefined),
    findOwned: jest.fn(),
    review: jest.fn().mockResolvedValue(undefined),
    reserveFinal: jest.fn().mockResolvedValue(undefined),
    completeFinal: jest.fn().mockResolvedValue(undefined),
    markAttemptFailed: jest.fn().mockResolvedValue(undefined),
    markCheckFailed: jest.fn().mockResolvedValue(undefined),
    markProcessingFailed: jest.fn().mockResolvedValue(undefined),
  };
  const storage = {
    activeProvider: 'LOCAL',
    write: jest.fn().mockResolvedValue(undefined),
    read: jest.fn().mockResolvedValue(Buffer.from('photo')),
  };
  const gateway = {
    execute: jest.fn(),
  };
  const processor = {
    process: jest.fn(),
  };
  const config = {
    get: jest.fn((key: keyof AppEnvironment) => {
      if (key === 'FILE_UPLOAD_MAX_BYTES') return 10 * 1024 * 1024;
      if (key === 'PUBLIC_API_URL') return 'https://api.example';
      return undefined;
    }),
  } as unknown as ConfigService<AppEnvironment, true>;
  const service = new TeacherPhotoService(
    repository as unknown as TeacherPhotoRepository,
    storage as unknown as FileStorageAdapter,
    gateway as unknown as AiGatewayService,
    processor as unknown as CreamBright04Processor,
    config,
  );
  return { service, repository, storage, gateway, processor };
}

function cameraFrame(width = 640, height = 360): Promise<Buffer> {
  return sharp({
    create: {
      width,
      height,
      channels: 3,
      background: '#cccccc',
    },
  })
    .jpeg()
    .toBuffer();
}

describe('TeacherPhotoService', () => {
  it('returns a pending response after safely storing the upload', async () => {
    const test = fixture();
    const pending = run();
    const image = await sharp({
      create: {
        width: 640,
        height: 360,
        channels: 3,
        background: '#cccccc',
      },
    })
      .jpeg()
      .toBuffer();
    test.repository.reserve.mockResolvedValue({
      type: 'CREATED',
      run: run({ status: 'UPLOADING' }),
    });
    test.repository.findOwned.mockResolvedValue(pending);

    await expect(
      test.service.submit(
        principal,
        pending.taskInstanceId,
        'teacher-photo-key',
        {
          buffer: image,
          originalname: 'camera.jpg',
          mimetype: 'image/jpeg',
          size: image.length,
        } as Express.Multer.File,
      ),
    ).resolves.toMatchObject({
      photoRunId: pending.photoRunId,
      status: 'CHECKING',
      decision: null,
    });

    expect(test.storage.write).toHaveBeenCalledTimes(1);
    expect(test.repository.markOriginalReady).toHaveBeenCalledTimes(1);
    expect(test.gateway.execute).not.toHaveBeenCalled();
    expect(test.processor.process).not.toHaveBeenCalled();
  });

  it('finishes AI review and image processing from a pending run', async () => {
    const test = fixture();
    const pending = run();
    test.storage.read.mockResolvedValue(await cameraFrame());
    test.gateway.execute.mockResolvedValue({
      status: 'SUCCEEDED',
      aiRunId: 'ai-run-001',
      content: JSON.stringify({
        decision: 'PASS',
        teacherReason: '符合要求',
        confidenceSummary: {},
        criteria: ['camera_angle', 'lighting', 'background', 'dressing'].map(
          (criterionKey) => ({
            criterionKey,
            result: 'PASS',
            confidence: 0.96,
            teacherMessage: null,
          }),
        ),
      }),
    });
    test.processor.process.mockResolvedValue({
      content: Buffer.from('processed-photo'),
      mimeType: 'image/jpeg',
      strength: 1,
      metrics: {},
    });

    await test.service.processPending(pending);

    expect(test.repository.review).toHaveBeenCalledWith(
      expect.objectContaining({
        photoRunId: pending.photoRunId,
        decision: 'PASS',
      }),
    );
    expect(test.repository.reserveFinal).toHaveBeenCalledWith(
      expect.objectContaining({
        photoRunId: pending.photoRunId,
        accountId: pending.accountId,
      }),
    );
    expect(test.repository.completeFinal).toHaveBeenCalledWith(
      pending.photoRunId,
      expect.any(String),
      pending.taskInstanceId,
      pending.processingOwner,
    );
  });

  it('resumes image processing after a restart without repeating AI review', async () => {
    const test = fixture();
    const pending = run({
      status: 'BEAUTIFYING',
      decision: 'PASS',
      finalFileId: 'final-file-001',
      finalStorageProvider: 'OSS',
      finalObjectKey: 'teacher-photos/account-001/final.jpg',
    });
    test.processor.process.mockResolvedValue({
      content: Buffer.from('processed-photo'),
      mimeType: 'image/jpeg',
      strength: 1,
      metrics: {},
    });

    await test.service.processPending(pending);

    expect(test.gateway.execute).not.toHaveBeenCalled();
    expect(test.repository.review).not.toHaveBeenCalled();
    expect(test.repository.reserveFinal).toHaveBeenCalledWith(
      expect.objectContaining({
        fileId: pending.finalFileId,
        objectKey: pending.finalObjectKey,
        storageProvider: pending.finalStorageProvider,
      }),
    );
    expect(test.repository.completeFinal).toHaveBeenCalledWith(
      pending.photoRunId,
      pending.finalFileId,
      pending.taskInstanceId,
      pending.processingOwner,
    );
  });

  it('backs off transient AI failures and releases the lease for retry', async () => {
    const test = fixture();
    const pending = run({ attemptCount: 2 });
    test.storage.read.mockResolvedValue(await cameraFrame());
    test.gateway.execute.mockResolvedValue({
      status: 'FAILED',
      aiRunId: 'ai-run-001',
      errorCode: 'AI_GATEWAY_UNAVAILABLE',
    });

    await test.service.processPending(pending);

    const [failure] = test.repository.markAttemptFailed.mock.calls[0] as [
      {
        photoRunId: string;
        processingOwner: string;
        attemptCount: number;
        stage: string;
        errorCode: string;
        retryDelayMs: number;
      },
    ];
    expect(failure).toMatchObject({
      photoRunId: pending.photoRunId,
      processingOwner: pending.processingOwner,
      attemptCount: 2,
      stage: 'CHECKING',
      errorCode: 'AI_GATEWAY_UNAVAILABLE',
    });
    expect(failure.retryDelayMs).toBeGreaterThan(0);
    expect(test.processor.process).not.toHaveBeenCalled();
  });
});

interface InternalTeacherPhotoService {
  parseAi(content: string): {
    decision: 'PASS' | 'RETRY' | 'ERROR';
    teacherMessage: string;
    confidenceSummary: Record<string, unknown>;
    checks: Array<{
      id: string;
      status: 'pass' | 'fail' | 'uncertain';
      message: string;
    }>;
  } | null;
  assertImageInput(upload: Express.Multer.File): Promise<void>;
  prepareAiImage(content: Buffer): Promise<Buffer>;
}

function createService(): InternalTeacherPhotoService {
  const config = {
    get: jest.fn((key: keyof AppEnvironment) =>
      key === 'FILE_UPLOAD_MAX_BYTES' ? 10_000_000 : 'https://api.example.test',
    ),
  } as unknown as ConfigService<AppEnvironment, true>;
  return new TeacherPhotoService(
    {} as TeacherPhotoRepository,
    {} as FileStorageAdapter,
    {} as AiGatewayService,
    {} as CreamBright04Processor,
    config,
  ) as unknown as InternalTeacherPhotoService;
}

function response(
  overrides: Partial<
    Record<
      'camera_angle' | 'lighting' | 'background' | 'dressing',
      { result: 'PASS' | 'FAIL' | 'UNKNOWN'; confidence: number }
    >
  > = {},
) {
  const defaultItem = { result: 'PASS' as const, confidence: 0.96 };
  return JSON.stringify({
    decision: 'PASS',
    teacherReason: '画面符合要求。',
    confidenceSummary: { overall: 0.96 },
    criteria: ['camera_angle', 'lighting', 'background', 'dressing'].map(
      (criterionKey) => ({
        criterionKey,
        ...defaultItem,
        ...overrides[
          criterionKey as
            'camera_angle' | 'lighting' | 'background' | 'dressing'
        ],
        teacherMessage: null,
      }),
    ),
  });
}

function upload(buffer: Buffer): Express.Multer.File {
  return {
    buffer,
    size: buffer.length,
    mimetype: 'image/jpeg',
    originalname: 'camera.jpg',
  } as Express.Multer.File;
}

describe('TeacherPhotoService strict camera review', () => {
  it('passes only when every item clears the confidence threshold', () => {
    const result = createService().parseAi(response());

    expect(result).toMatchObject({
      decision: 'PASS',
      checks: [
        { id: 'camera_angle', status: 'pass' },
        { id: 'lighting', status: 'pass' },
        { id: 'background', status: 'pass' },
        { id: 'dressing', status: 'pass' },
      ],
    });
    expect(result?.confidenceSummary).toMatchObject({
      minimumRequired: TEACHER_PHOTO_MIN_CONFIDENCE,
      minimumObserved: 0.96,
    });
  });

  it('requires a retake when a nominal PASS is below 85% confidence', () => {
    const result = createService().parseAi(
      response({
        lighting: {
          result: 'PASS',
          confidence: TEACHER_PHOTO_MIN_CONFIDENCE - 0.01,
        },
      }),
    );

    expect(result?.decision).toBe('RETRY');
    const lighting = result?.checks.find((check) => check.id === 'lighting');
    expect(lighting?.status).toBe('uncertain');
    expect(lighting?.message).toContain('84%');
  });

  it('never trusts an overall PASS when one item fails', () => {
    const result = createService().parseAi(
      response({ background: { result: 'FAIL', confidence: 0.99 } }),
    );

    expect(result?.decision).toBe('RETRY');
    expect(
      result?.checks.find((check) => check.id === 'background')?.status,
    ).toBe('fail');
  });

  it('treats missing confidence as uncertain instead of passing', () => {
    const result = createService().parseAi(
      response().replace('"confidence":0.96,', ''),
    );

    expect(result?.decision).toBe('RETRY');
    expect(result?.checks[0].status).toBe('uncertain');
  });

  it('accepts only readable 16:9 images of at least 640×360', async () => {
    const service = createService();
    const valid = await sharp({
      create: {
        width: 640,
        height: 360,
        channels: 3,
        background: '#cccccc',
      },
    })
      .jpeg()
      .toBuffer();
    const lowResolution = await sharp({
      create: {
        width: 320,
        height: 180,
        channels: 3,
        background: '#cccccc',
      },
    })
      .jpeg()
      .toBuffer();
    const wrongRatio = await sharp({
      create: {
        width: 640,
        height: 480,
        channels: 3,
        background: '#cccccc',
      },
    })
      .jpeg()
      .toBuffer();

    await expect(service.assertImageInput(upload(valid))).resolves.toBe(
      undefined,
    );
    await expect(
      service.assertImageInput(upload(lowResolution)),
    ).rejects.toBeInstanceOf(BadRequestException);
    await expect(
      service.assertImageInput(upload(wrongRatio)),
    ).rejects.toBeInstanceOf(BadRequestException);
    await expect(
      service.assertImageInput(upload(Buffer.from('not-an-image'))),
    ).rejects.toBeInstanceOf(BadRequestException);
  });

  it('bounds the inline AI image before base64 encoding', async () => {
    const service = createService();
    const large = await cameraFrame(2560, 1440);

    const prepared = await service.prepareAiImage(large);
    const metadata = await sharp(prepared).metadata();

    expect(metadata.width).toBeLessThanOrEqual(1280);
    expect(metadata.height).toBeLessThanOrEqual(720);
    expect(metadata.format).toBe('jpeg');
  });
});
