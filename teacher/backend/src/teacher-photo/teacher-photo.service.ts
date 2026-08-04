import {
  BadRequestException,
  ConflictException,
  Injectable,
  Logger,
  NotFoundException,
  PayloadTooLargeException,
  UnsupportedMediaTypeException,
} from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { createHash, randomUUID } from 'node:crypto';
import sharp from 'sharp';
import { z } from 'zod';
import type { AuthPrincipal } from '../auth/auth.models';
import { FileStorageAdapter } from '../files/file-storage.adapter';
import { AiGatewayService } from '../integrations/ai/ai-gateway.service';
import type { AppEnvironment } from '../platform/config/environment';
import { JobLeaseLostError } from '../platform/database/job-lease.service';
import {
  CREAM_BRIGHT_04_PRESET,
  CreamBright04Processor,
} from './cream-bright-04.processor';
import type {
  TeacherPhotoCheck,
  TeacherPhotoRunRecord,
  TeacherPhotoRunResponse,
} from './teacher-photo.models';
import { TeacherPhotoRepository } from './teacher-photo.repository';

export const TEACHER_PHOTO_CRITERIA_VERSION =
  'lesson-preparation-camera-view-2026-07-v3-strict';
export const TEACHER_PHOTO_MIN_CONFIDENCE = 0.85;
export const TEACHER_PHOTO_MIN_WIDTH = 640;
export const TEACHER_PHOTO_MIN_HEIGHT = 360;

const criteria = [
  {
    id: 'camera_angle',
    title: '摄像头角度',
    suggestion:
      '画面中只保留一位老师，面部无遮挡且清晰完整，头顶留有少量空间，胸部以上入镜；人物居中并让镜头与眼睛大致平齐。',
  },
  {
    id: 'lighting',
    title: '光线',
    suggestion:
      '让面部两侧五官都能清楚辨认，使用正面均匀光线；避免明显过暗、重阴影、过曝、强逆光或滤镜遮盖。',
  },
  {
    id: 'background',
    title: '背景',
    suggestion:
      '背景需整洁稳定，不出现无关人员、动物、明显杂物或敏感个人信息；虚拟背景不得破损、穿帮或遮挡人物。',
  },
  {
    id: 'dressing',
    title: '着装',
    suggestion:
      '穿着整洁、专业且适合正式授课，上衣与肩颈区域需清楚可见；睡衣、居家服、无袖、明显过于休闲或干扰性强的服饰不通过。',
  },
] as const;

const aiResponseSchema = z
  .object({
    decision: z.enum(['PASS', 'RETRY', 'ERROR']),
    teacherReason: z.string().min(1).max(2_000),
    confidenceSummary: z.record(z.string(), z.unknown()).default({}),
    criteria: z
      .array(
        z
          .object({
            criterionKey: z.string(),
            result: z.enum(['PASS', 'FAIL', 'UNKNOWN']),
            teacherMessage: z.string().max(2_000).nullable().default(null),
            confidence: z.number().min(0).max(1).default(0),
          })
          .strict(),
      )
      .length(criteria.length),
  })
  .strict();

const systemPrompt = `你是新师训练营的摄像头画面审核器。只根据输入的当前摄像头画面进行判断，必须严格输出 JSON，不得输出 Markdown。不要判断或推断年龄、种族、健康、宗教等敏感属性。\n
先执行硬性前置检查：画面必须可读取、为清晰的 16:9 横向照片、只出现一位老师；老师的面部主要轮廓从额头到下巴完整可见，没有被手、手机、口罩、墨镜或画面边缘明显遮挡，且不存在严重模糊、马赛克或压缩失真。无法读取时 decision=ERROR；其余任一前置条件不满足时，将 camera_angle 或对应项目判为 FAIL，并 decision=RETRY。\n
逐项严格检查以下四项，不能因为画面中有人就默认通过：\n
1 camera_angle：一位老师的头部和胸部以上完整入镜，头顶有少量留白，头部高度约占画面高度 20%–40%，人物位于画面中央区域，镜头与眼睛大致平齐；过近、过远、明显偏离中心、裁切头顶或下巴、机位明显过高或过低时 FAIL；\n
2 lighting：面部两侧五官均清楚可辨，亮度均匀且肤色没有被滤镜或高光遮盖；明显过暗、重阴影、过曝、强逆光或面部细节不可辨时 FAIL；\n
3 background：背景整洁稳定，不得出现无关人员、动物、床铺衣物等明显杂物或可识别的敏感个人信息；虚拟背景可以通过，但破损、穿帮、遮挡人物或画面混乱时 FAIL；\n
4 dressing：只判断画面中可见的衣着是否整洁、专业并适合正式授课，肩颈与上衣需清楚可见；睡衣、居家服、无袖、明显过于休闲、衣着凌乱或干扰性强的服饰与配饰时 FAIL。\n
每项同时给出 0 到 1 的 confidence。只有证据清楚且 confidence >= ${TEACHER_PHOTO_MIN_CONFIDENCE} 才能将该项标为 PASS；证据不足或 confidence 低于 ${TEACHER_PHOTO_MIN_CONFIDENCE} 时必须标为 UNKNOWN。任何一项 FAIL 或 UNKNOWN，decision 必须是 RETRY；只有四项全部 PASS 才能 decision=PASS。不要检查耳麦、麦克风、扬声器、网络、噪音或设备性能。\n
严格格式：{"decision":"PASS|RETRY|ERROR","teacherReason":"给老师的简短说明","confidenceSummary":{},"criteria":[{"criterionKey":"camera_angle","result":"PASS|FAIL|UNKNOWN","teacherMessage":null,"confidence":0.95}]}。criteria 必须且只能包含 camera_angle、lighting、background、dressing，各一次。`;

class TeacherPhotoProcessingError extends Error {
  constructor(readonly code: string) {
    super(code);
    this.name = 'TeacherPhotoProcessingError';
  }
}

@Injectable()
export class TeacherPhotoService {
  private readonly logger = new Logger(TeacherPhotoService.name);
  private readonly maxBytes: number;
  private readonly publicApiUrl: string;

  constructor(
    private readonly repository: TeacherPhotoRepository,
    private readonly storage: FileStorageAdapter,
    private readonly gateway: AiGatewayService,
    private readonly processor: CreamBright04Processor,
    config: ConfigService<AppEnvironment, true>,
  ) {
    this.maxBytes = config.get('FILE_UPLOAD_MAX_BYTES', { infer: true });
    this.publicApiUrl = config
      .get('PUBLIC_API_URL', { infer: true })
      .replace(/\/$/, '');
  }

  async submit(
    principal: AuthPrincipal,
    taskInstanceId: string,
    idempotencyKey: string | undefined,
    upload: Express.Multer.File | undefined,
  ): Promise<TeacherPhotoRunResponse> {
    this.assertInput(idempotencyKey, upload);
    const file = upload!;
    await this.assertImageInput(file);
    const requestHash = createHash('sha256').update(file.buffer).digest('hex');
    const photoRunSeed = randomUUID();
    const originalFileId = randomUUID();
    const originalObjectKey = `teacher-photos/${principal.accountId}/${photoRunSeed}/original`;
    const reserved = await this.repository.reserve({
      accountId: principal.accountId,
      taskInstanceId,
      idempotencyKey: idempotencyKey!,
      requestHash,
      originalFileId,
      storageProvider: this.storage.activeProvider,
      originalObjectKey,
      originalFilename: this.filename(file.originalname),
      mimeType: file.mimetype.toLowerCase(),
      sizeBytes: file.size,
      sha256: requestHash,
      criteriaVersion: TEACHER_PHOTO_CRITERIA_VERSION,
    });
    if (reserved.type === 'TASK_NOT_FOUND') {
      throw new NotFoundException({
        code: 'TEACHER_PHOTO_TASK_NOT_FOUND',
        message: '未找到首课准备中的摄像头画面检测步骤',
        retryable: false,
      });
    }
    if (reserved.type === 'IDEMPOTENCY_CONFLICT') {
      throw new ConflictException({
        code: 'IDEMPOTENCY_KEY_REUSED',
        message: '该幂等键已用于另一张照片',
        retryable: false,
      });
    }
    if (reserved.type !== 'CREATED') return this.response(reserved.run);

    try {
      await this.storage.write(
        reserved.run.originalObjectKey,
        file.buffer,
        reserved.run.originalStorageProvider,
      );
      await this.repository.markOriginalReady(
        reserved.run.photoRunId,
        reserved.run.originalFileId,
      );
    } catch {
      await this.repository.markUploadFailed(
        reserved.run.photoRunId,
        reserved.run.originalFileId,
      );
      throw new ConflictException({
        code: 'TEACHER_PHOTO_STORAGE_FAILED',
        message: '照片暂时无法安全保存，请稍后重新提交',
        retryable: true,
      });
    }

    return this.latest(principal, taskInstanceId);
  }

  async processPending(
    run: TeacherPhotoRunRecord,
    assertLeaseActive: () => void = () => undefined,
  ): Promise<void> {
    if (!run.processingOwner) {
      this.logger.warn({
        event: 'teacher_photo_processing_skipped_without_lease',
        photoRunId: run.photoRunId,
      });
      return;
    }
    const startedAt = Date.now();
    let stage: 'CHECKING' | 'BEAUTIFYING' =
      run.status === 'BEAUTIFYING' ? 'BEAUTIFYING' : 'CHECKING';
    let finalFileId = run.finalFileId ?? undefined;
    try {
      assertLeaseActive();
      const content = await this.storage.read(
        run.originalObjectKey,
        run.originalStorageProvider,
      );
      assertLeaseActive();

      if (run.status === 'CHECKING') {
        const review = await this.review(run.photoRunId, {
          content,
          filename: run.originalFilename,
          mimeType: run.originalMimeType,
        });
        assertLeaseActive();
        const reviewSaved = await this.repository.review({
          photoRunId: run.photoRunId,
          processingOwner: run.processingOwner,
          aiRunId: review.aiRunId,
          decision: review.decision,
          teacherMessage: review.teacherMessage,
          checks: review.checks,
          confidenceSummary: review.confidenceSummary,
        });
        if (reviewSaved === false) return;
        if (review.decision !== 'PASS') {
          this.logger.log({
            event: 'teacher_photo_review_finished',
            photoRunId: run.photoRunId,
            decision: review.decision,
            durationMs: Date.now() - startedAt,
          });
          return;
        }
        stage = 'BEAUTIFYING';
      }

      assertLeaseActive();
      const processed = await this.processor.process(content);
      assertLeaseActive();
      finalFileId ??= randomUUID();
      const finalObjectKey =
        run.finalObjectKey ??
        `teacher-photos/${run.accountId}/${run.photoRunId}/cream-bright-04.jpg`;
      const finalStorageProvider =
        run.finalStorageProvider ?? this.storage.activeProvider;
      const finalHash = createHash('sha256')
        .update(processed.content)
        .digest('hex');
      const finalReserved = await this.repository.reserveFinal({
        photoRunId: run.photoRunId,
        processingOwner: run.processingOwner,
        accountId: run.accountId,
        fileId: finalFileId,
        objectKey: finalObjectKey,
        filename: 'teacher-standard-photo.jpg',
        mimeType: processed.mimeType,
        sizeBytes: processed.content.length,
        sha256: finalHash,
        storageProvider: finalStorageProvider,
        filterPreset: CREAM_BRIGHT_04_PRESET,
        filterStrength: processed.strength,
        sourceMetrics: processed.metrics,
      });
      if (finalReserved === false) return;
      assertLeaseActive();
      await this.storage.write(
        finalObjectKey,
        processed.content,
        finalStorageProvider,
      );
      assertLeaseActive();
      const completed = await this.repository.completeFinal(
        run.photoRunId,
        finalFileId,
        run.taskInstanceId,
        run.processingOwner,
      );
      if (completed === false) return;
      this.logger.log({
        event: 'teacher_photo_processing_finished',
        photoRunId: run.photoRunId,
        decision: 'PASS',
        durationMs: Date.now() - startedAt,
      });
    } catch (error) {
      if (error instanceof JobLeaseLostError) {
        this.logger.warn({
          event: 'teacher_photo_processing_stopped_after_lease_loss',
          photoRunId: run.photoRunId,
          stage,
          durationMs: Date.now() - startedAt,
        });
        return;
      }
      const attemptCount = Math.max(1, run.attemptCount ?? 1);
      const baseDelayMs = Math.min(30_000, 2_000 * 2 ** (attemptCount - 1));
      const retryDelayMs = Math.round(
        baseDelayMs * (0.75 + Math.random() * 0.5),
      );
      const errorCode =
        error instanceof TeacherPhotoProcessingError
          ? error.code
          : stage === 'CHECKING'
            ? 'TEACHER_PHOTO_BACKGROUND_CHECK_FAILED'
            : 'CREAM_BRIGHT_04_PROCESSING_FAILED';
      await this.repository.markAttemptFailed({
        photoRunId: run.photoRunId,
        processingOwner: run.processingOwner,
        attemptCount,
        stage,
        errorCode,
        teacherMessage:
          stage === 'CHECKING'
            ? '自动画面检测暂时未能完成，本次照片已保留，请稍后重新检测。'
            : '照片优化暂时未能完成，本次照片已保留，请稍后再试。',
        retryDelayMs,
        fileId: finalFileId,
      });
      this.logger.error({
        event: 'teacher_photo_processing_failed',
        photoRunId: run.photoRunId,
        stage,
        durationMs: Date.now() - startedAt,
        errorCode,
        attemptCount,
      });
    }
  }

  async latest(
    principal: AuthPrincipal,
    taskInstanceId: string,
  ): Promise<TeacherPhotoRunResponse> {
    const run = await this.repository.findOwned(
      principal.accountId,
      taskInstanceId,
    );
    if (!run) {
      throw new NotFoundException({
        code: 'TEACHER_PHOTO_NOT_FOUND',
        message: '还没有摄像头画面检测记录',
        retryable: false,
      });
    }
    return this.response(run);
  }

  async content(principal: AuthPrincipal, taskInstanceId: string) {
    const record = await this.repository.findReadyContent(
      principal.accountId,
      taskInstanceId,
    );
    if (!record) {
      throw new NotFoundException({
        code: 'TEACHER_PHOTO_FINAL_NOT_FOUND',
        message: '画面检测证据尚未生成',
        retryable: true,
      });
    }
    return {
      ...record,
      content: await this.storage.read(
        record.objectKey,
        record.storageProvider,
      ),
    };
  }

  private async review(
    photoRunId: string,
    file: { content: Buffer; filename: string; mimeType: string },
  ) {
    const aiImage = await this.prepareAiImage(file.content);
    const execution = await this.gateway.execute({
      capability: 'TASK_IMAGE_REVIEW',
      callerModule: 'TEACHER_PHOTO',
      promptVersionId: null,
      systemPrompt,
      userText: `审核首课准备的当前摄像头画面。photoRunId=${photoRunId}，criteriaVersion=${TEACHER_PHOTO_CRITERIA_VERSION}。`,
      file: {
        content: aiImage,
        filename: file.filename.replace(/\.[^.]+$/, '') + '-ai.jpg',
        mimeType: 'image/jpeg',
      },
    });
    if (execution.status === 'FAILED') {
      throw new TeacherPhotoProcessingError(execution.errorCode);
    }
    const parsed = this.parseAi(execution.content);
    if (!parsed) {
      throw new TeacherPhotoProcessingError(
        'TEACHER_PHOTO_AI_RESPONSE_INVALID',
      );
    }
    return { aiRunId: execution.aiRunId, ...parsed };
  }

  private prepareAiImage(content: Buffer): Promise<Buffer> {
    return sharp(content, { failOn: 'warning' })
      .rotate()
      .resize({
        width: 1280,
        height: 720,
        fit: 'inside',
        withoutEnlargement: true,
      })
      .jpeg({ quality: 85, mozjpeg: true })
      .toBuffer();
  }

  private parseAi(content: string) {
    let json: unknown;
    try {
      json = JSON.parse(content);
    } catch {
      return null;
    }
    const parsed = aiResponseSchema.safeParse(json);
    if (!parsed.success) return null;
    const byId = new Map(
      parsed.data.criteria.map((item) => [item.criterionKey, item]),
    );
    if (
      byId.size !== criteria.length ||
      criteria.some((item) => !byId.has(item.id))
    )
      return null;
    const checks: TeacherPhotoCheck[] = criteria.map((criterion) => {
      const item = byId.get(criterion.id)!;
      const confidencePassed =
        item.result === 'PASS' &&
        item.confidence >= TEACHER_PHOTO_MIN_CONFIDENCE;
      return {
        id: criterion.id,
        title: criterion.title,
        status: confidencePassed
          ? 'pass'
          : item.result === 'FAIL'
            ? 'fail'
            : 'uncertain',
        message:
          item.teacherMessage ||
          (confidencePassed
            ? '符合当前严格标准'
            : item.result === 'PASS'
              ? `判断把握不足（${Math.round(item.confidence * 100)}%），需要重新拍照`
              : '需要调整后重新拍照'),
        suggestion: confidencePassed ? '' : criterion.suggestion,
      };
    });
    const allPass = checks.every((item) => item.status === 'pass');
    const decision =
      parsed.data.decision === 'ERROR'
        ? 'ERROR'
        : allPass && parsed.data.decision === 'PASS'
          ? 'PASS'
          : 'RETRY';
    return {
      decision,
      teacherMessage:
        decision === 'RETRY' &&
        parsed.data.criteria.some(
          (item) =>
            item.result === 'PASS' &&
            item.confidence < TEACHER_PHOTO_MIN_CONFIDENCE,
        )
          ? '有项目未达到可靠判断阈值，请按提示调整后重新拍照。'
          : parsed.data.teacherReason,
      confidenceSummary: {
        ...parsed.data.confidenceSummary,
        minimumRequired: TEACHER_PHOTO_MIN_CONFIDENCE,
        minimumObserved: Math.min(
          ...parsed.data.criteria.map((item) => item.confidence),
        ),
        criteria: Object.fromEntries(
          parsed.data.criteria.map((item) => [
            item.criterionKey,
            item.confidence,
          ]),
        ),
      },
      checks,
    } as const;
  }

  private unknownChecks(message: string): TeacherPhotoCheck[] {
    return criteria.map((criterion) => ({
      id: criterion.id,
      title: criterion.title,
      status: 'uncertain',
      message,
      suggestion: criterion.suggestion,
    }));
  }

  private response(run: TeacherPhotoRunRecord): TeacherPhotoRunResponse {
    return {
      photoRunId: run.photoRunId,
      status: run.status,
      decision: run.decision,
      criteriaVersion: run.criteriaVersion,
      teacherMessage: run.teacherMessage,
      submittedAt: run.submittedAt.toISOString(),
      checkedAt: run.checkedAt?.toISOString() ?? null,
      checks: run.checks,
      finalPhoto:
        run.status === 'READY' &&
        run.finalFileId &&
        run.filterPreset &&
        run.filterStrength !== null &&
        run.processedAt
          ? {
              fileId: run.finalFileId,
              contentUrl: `${this.publicApiUrl}/api/v1/tasks/${encodeURIComponent(run.taskInstanceId)}/teacher-photo/content`,
              filterPreset: run.filterPreset,
              filterStrength: run.filterStrength,
              savedAt: run.processedAt.toISOString(),
            }
          : null,
    };
  }

  private assertInput(
    idempotencyKey: string | undefined,
    upload: Express.Multer.File | undefined,
  ): void {
    if (
      !idempotencyKey ||
      idempotencyKey.length < 8 ||
      idempotencyKey.length > 128
    ) {
      throw new BadRequestException({
        code: 'INVALID_IDEMPOTENCY_KEY',
        message: 'Idempotency-Key 长度必须为 8 到 128 个字符',
        retryable: false,
      });
    }
    if (!upload) {
      throw new BadRequestException({
        code: 'TEACHER_PHOTO_REQUIRED',
        message: '请使用 photo 字段提交当前摄像头画面',
        retryable: false,
      });
    }
    if (upload.size > this.maxBytes) {
      throw new PayloadTooLargeException({
        code: 'TEACHER_PHOTO_TOO_LARGE',
        message: `照片大小不能超过 ${this.maxBytes} 字节`,
        retryable: false,
      });
    }
    if (
      !['image/jpeg', 'image/png', 'image/webp'].includes(
        upload.mimetype.toLowerCase(),
      )
    ) {
      throw new UnsupportedMediaTypeException({
        code: 'TEACHER_PHOTO_TYPE_INVALID',
        message: '只支持 JPG、PNG 或 WEBP 照片',
        retryable: false,
      });
    }
  }

  private async assertImageInput(upload: Express.Multer.File): Promise<void> {
    let metadata: Awaited<ReturnType<ReturnType<typeof sharp>['metadata']>>;
    try {
      metadata = await sharp(upload.buffer, { failOn: 'warning' }).metadata();
    } catch {
      throw new BadRequestException({
        code: 'TEACHER_PHOTO_IMAGE_INVALID',
        message: '照片无法读取，请重新打开摄像头拍摄',
        retryable: false,
      });
    }
    let width = metadata.width ?? 0;
    let height = metadata.height ?? 0;
    if (
      metadata.orientation &&
      metadata.orientation >= 5 &&
      metadata.orientation <= 8
    ) {
      [width, height] = [height, width];
    }
    if (width < TEACHER_PHOTO_MIN_WIDTH || height < TEACHER_PHOTO_MIN_HEIGHT) {
      throw new BadRequestException({
        code: 'TEACHER_PHOTO_DIMENSIONS_INVALID',
        message: `照片至少需要 ${TEACHER_PHOTO_MIN_WIDTH}×${TEACHER_PHOTO_MIN_HEIGHT} 像素，请使用清晰摄像头重新拍摄`,
        retryable: false,
      });
    }
    const ratio = width / height;
    if (ratio < 1.7 || ratio > 1.82) {
      throw new BadRequestException({
        code: 'TEACHER_PHOTO_FRAME_INVALID',
        message: '请使用 16:9 横向画面重新拍摄',
        retryable: false,
      });
    }
  }

  private filename(value: string): string {
    const safe = [...value]
      .filter((character) => {
        const code = character.charCodeAt(0);
        return (
          code >= 32 && code !== 127 && character !== '/' && character !== '\\'
        );
      })
      .join('')
      .trim();
    return safe.slice(0, 255) || 'teacher-photo.jpg';
  }
}
