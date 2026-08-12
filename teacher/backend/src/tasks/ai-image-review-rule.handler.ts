import { Injectable } from '@nestjs/common';
import { z } from 'zod';
import { FileStorageAdapter } from '../files/file-storage.adapter';
import { AiGatewayService } from '../integrations/ai/ai-gateway.service';
import { parseAiJsonObject } from '../integrations/ai/ai-json-response';
import {
  centralExposureFailure,
  prepareAiReviewImage,
  type CentralExposureSignals,
} from '../integrations/ai/image-exposure-signals';
import {
  TEACHER_PHOTO_CRITERIA_KEYS,
  TEACHER_PHOTO_CRITERIA_VERSION,
  TEACHER_PHOTO_MIN_CONFIDENCE,
  TEACHER_PHOTO_SYSTEM_PROMPT,
  TEACHER_PHOTO_USER_TEXT,
  teacherPhotoBackgroundVetoMessage,
  teacherPhotoMinimumConfidence,
} from '../teacher-photo/teacher-photo-review.policy';
import { TaskRuleHandler } from './task-rule.handler';
import {
  ImageReviewRepository,
  type ImageReviewItem,
} from './image-review.repository';
import type { TaskRuleContext, TaskRuleResult } from './task-validation.models';

const ruleConfigSchema = z
  .object({
    stepKey: z.string().min(1).max(128),
    reviewProfile: z.enum(['TEACHING_ENVIRONMENT_V1']).optional(),
    promptVersionId: z.string().uuid().optional(),
    criteriaVersion: z.string().min(1).max(128),
    criteriaKeys: z.array(z.string().min(1).max(128)).min(1).max(50),
    allowedMimeTypes: z
      .array(z.string().min(1).max(128))
      .min(1)
      .max(20)
      .default(['image/jpeg', 'image/png', 'image/webp']),
    systemPrompt: z.string().min(1).max(50_000),
    userText: z.string().min(1).max(2_000),
  })
  .strict();

const reviewResponseSchema = z
  .object({
    decision: z.enum(['PASS', 'RETRY', 'ERROR']),
    teacherReason: z.string().min(1).max(2_000),
    confidenceSummary: z.record(z.string(), z.unknown()).default({}),
    criteria: z
      .array(
        z
          .object({
            criterionKey: z.string().min(1).max(128),
            result: z.enum(['PASS', 'FAIL', 'UNKNOWN']),
            teacherMessage: z.string().max(2_000).nullable().default(null),
            confidence: z.number().min(0).max(1).optional(),
          })
          .strict(),
      )
      .min(1)
      .max(50),
  })
  .strict();

const technicalTeacherMessage =
  '暂时无法完成自动检查，本次提交已转为人工复核。';
const ephemeralTechnicalTeacherMessage =
  '暂时无法完成自动检查，请重新拍照后重试。';
const lowConfidenceTeacherMessage = '判断把握不足，请按提示调整后重新拍照。';
const currentG04PhotoStepKey = 'g02-environment-photo';
const teachingEnvironmentReviewProfile = 'TEACHING_ENVIRONMENT_V1';
const neutralExposure: CentralExposureSignals = {
  centralMeanLuma: 128,
  centralClippedLumaRatio: 0,
};

@Injectable()
export class AiImageReviewRuleHandler extends TaskRuleHandler {
  readonly ruleType = 'AI_IMAGE_REVIEW';

  constructor(
    private readonly storage: FileStorageAdapter,
    private readonly gateway: AiGatewayService,
    private readonly reviews: ImageReviewRepository,
  ) {
    super();
  }

  async evaluate(context: TaskRuleContext): Promise<TaskRuleResult> {
    const parsedConfig = ruleConfigSchema.safeParse(context.rule.config);
    if (!parsedConfig.success) {
      return this.deferred('IMAGE_REVIEW_CONFIG_INVALID');
    }
    const config = parsedConfig.data;
    const isCurrentG04 = config.stepKey === currentG04PhotoStepKey;
    const isEphemeralPersonalizedReview =
      config.reviewProfile === teachingEnvironmentReviewProfile &&
      !isCurrentG04;
    const usesTeachingEnvironmentProfile =
      config.reviewProfile === teachingEnvironmentReviewProfile || isCurrentG04;
    const criteriaVersion = isCurrentG04
      ? TEACHER_PHOTO_CRITERIA_VERSION
      : config.criteriaVersion;
    const criteriaKeys = usesTeachingEnvironmentProfile
      ? [...TEACHER_PHOTO_CRITERIA_KEYS]
      : config.criteriaKeys;
    const minimumConfidence = usesTeachingEnvironmentProfile
      ? TEACHER_PHOTO_MIN_CONFIDENCE
      : 0;
    if (
      new Set(criteriaKeys).size !== criteriaKeys.length ||
      (config.promptVersionId &&
        !(await this.reviews.isActivePromptVersion(
          context.client,
          config.promptVersionId,
        )))
    ) {
      return this.deferred('IMAGE_REVIEW_CONFIG_INVALID');
    }

    const output = context.outputs.find(
      (candidate) =>
        candidate.stepKey === config.stepKey && candidate.outputType === 'FILE',
    );
    const fileId = output?.value.fileId;
    if (typeof fileId !== 'string') {
      return this.deferred('IMAGE_REVIEW_FILE_MISSING');
    }
    const file = await this.reviews.findSubmissionFile(context.client, {
      accountId: context.accountId,
      taskInstanceId: context.taskInstanceId,
      submissionId: context.submissionId,
      stepKey: config.stepKey,
      fileId,
    });
    if (!file || !config.allowedMimeTypes.includes(file.mimeType)) {
      return this.deferred('IMAGE_REVIEW_FILE_INVALID');
    }
    if (
      !isEphemeralPersonalizedReview &&
      usesTeachingEnvironmentProfile &&
      (await this.reviews.hasPassedReview(context.client, {
        fileId,
        criteriaVersion,
      }))
    ) {
      return {
        passed: true,
        resultCode: 'IMAGE_REVIEW_PASSED',
        teacherMessage: null,
      };
    }

    let content: Buffer;
    try {
      content = await this.storage.read(file.objectKey, file.storageProvider);
    } catch {
      return this.handleTechnicalError(
        context,
        fileId,
        criteriaVersion,
        criteriaKeys,
        null,
        'IMAGE_REVIEW_FILE_UNAVAILABLE',
        isEphemeralPersonalizedReview,
      );
    }
    if (isEphemeralPersonalizedReview) {
      try {
        await this.storage.delete(file.objectKey, file.storageProvider);
      } catch {
        return this.handleTechnicalError(
          context,
          fileId,
          criteriaVersion,
          criteriaKeys,
          null,
          'IMAGE_REVIEW_FILE_DELETE_FAILED',
          true,
        );
      }
    }
    let reviewContent = content;
    let reviewFilename = file.originalFilename;
    let reviewMimeType = file.mimeType;
    let exposure = neutralExposure;
    if (usesTeachingEnvironmentProfile) {
      try {
        const prepared = await prepareAiReviewImage(content, {
          includeCameraGuide: true,
        });
        reviewContent = prepared.content;
        reviewFilename =
          file.originalFilename.replace(/\.[^.]+$/, '') + '-ai.jpg';
        reviewMimeType = 'image/jpeg';
        exposure = prepared.exposure;
      } catch {
        return this.handleTechnicalError(
          context,
          fileId,
          criteriaVersion,
          criteriaKeys,
          null,
          'IMAGE_REVIEW_FILE_INVALID',
          isEphemeralPersonalizedReview,
        );
      }
    }
    const execution = await this.gateway.execute({
      capability: 'TASK_IMAGE_REVIEW',
      callerModule: 'TASK_VALIDATION',
      promptVersionId: config.promptVersionId ?? null,
      systemPrompt: isCurrentG04
        ? TEACHER_PHOTO_SYSTEM_PROMPT
        : config.systemPrompt,
      userText: isCurrentG04 ? TEACHER_PHOTO_USER_TEXT : config.userText,
      file: {
        content: reviewContent,
        filename: reviewFilename,
        mimeType: reviewMimeType,
      },
    });
    if (execution.status === 'FAILED') {
      return this.handleTechnicalError(
        context,
        fileId,
        criteriaVersion,
        criteriaKeys,
        execution.aiRunId,
        execution.errorCode,
        isEphemeralPersonalizedReview,
      );
    }

    const parsedReview = this.parseReview(
      execution.content,
      criteriaKeys,
      minimumConfidence,
      exposure,
      usesTeachingEnvironmentProfile,
    );
    if (!parsedReview) {
      return this.handleTechnicalError(
        context,
        fileId,
        criteriaVersion,
        criteriaKeys,
        execution.aiRunId,
        'IMAGE_REVIEW_RESPONSE_INVALID',
        isEphemeralPersonalizedReview,
      );
    }
    if (!isEphemeralPersonalizedReview) {
      await this.reviews.save(context.client, {
        fileId,
        submissionId: context.submissionId,
        aiRunId: execution.aiRunId,
        criteriaVersion,
        decision: parsedReview.decision,
        teacherReason: parsedReview.teacherReason,
        confidenceSummary: parsedReview.confidenceSummary,
        items: parsedReview.criteria,
      });
    }
    if (parsedReview.decision === 'ERROR') {
      return isEphemeralPersonalizedReview
        ? {
            passed: false,
            resultCode: 'IMAGE_REVIEW_ERROR',
            teacherMessage: ephemeralTechnicalTeacherMessage,
          }
        : this.deferred('IMAGE_REVIEW_ERROR', parsedReview.teacherReason);
    }
    return {
      passed: parsedReview.decision === 'PASS',
      resultCode:
        parsedReview.decision === 'PASS'
          ? 'IMAGE_REVIEW_PASSED'
          : 'IMAGE_REVIEW_RETRY',
      teacherMessage: parsedReview.teacherReason,
    };
  }

  private parseReview(
    content: string,
    criteriaKeys: string[],
    minimumConfidence: number,
    exposure: CentralExposureSignals,
    usesTeachingEnvironmentProfile: boolean,
  ): z.infer<typeof reviewResponseSchema> | null {
    const json = parseAiJsonObject(content);
    const parsed = reviewResponseSchema.safeParse(json);
    if (!parsed.success) {
      return null;
    }
    const actualKeys = parsed.data.criteria.map((item) => item.criterionKey);
    if (
      new Set(actualKeys).size !== actualKeys.length ||
      actualKeys.length !== criteriaKeys.length ||
      criteriaKeys.some((key) => !actualKeys.includes(key))
    ) {
      return null;
    }
    let thresholdDowngraded = false;
    let criteria = parsed.data.criteria.map((item) => {
      const requiredConfidence = usesTeachingEnvironmentProfile
        ? teacherPhotoMinimumConfidence(item.criterionKey)
        : minimumConfidence;
      if (
        item.result === 'PASS' &&
        requiredConfidence > 0 &&
        (item.confidence ?? 0) < requiredConfidence
      ) {
        thresholdDowngraded = true;
        return {
          ...item,
          result: 'UNKNOWN' as const,
          teacherMessage: item.teacherMessage ?? lowConfidenceTeacherMessage,
        };
      }
      return item;
    });
    const backgroundVetoMessage = usesTeachingEnvironmentProfile
      ? teacherPhotoBackgroundVetoMessage(parsed.data.confidenceSummary)
      : null;
    if (backgroundVetoMessage) {
      criteria = criteria.map((item) =>
        item.criterionKey !== 'background'
          ? item
          : {
              ...item,
              result: 'FAIL' as const,
              teacherMessage: backgroundVetoMessage,
            },
      );
    }
    const exposureIssue = centralExposureFailure(exposure);
    const cameraPassed =
      criteria.find((item) => item.criterionKey === 'camera_angle')?.result ===
      'PASS';
    if (cameraPassed && exposureIssue) {
      criteria = criteria.map((item) =>
        item.criterionKey !== 'lighting' || item.result !== 'PASS'
          ? item
          : {
              ...item,
              result: 'FAIL' as const,
              teacherMessage:
                exposureIssue === 'OVEREXPOSED'
                  ? '画面中央区域存在明显过曝或强眩光。'
                  : '画面中央区域明显过暗。',
            },
      );
    }
    const allCriteriaPass = criteria.every((item) => item.result === 'PASS');
    const confidences = criteria
      .map((item) => item.confidence)
      .filter((value): value is number => typeof value === 'number');
    const decision =
      parsed.data.decision === 'ERROR'
        ? 'ERROR'
        : parsed.data.decision === 'PASS' && allCriteriaPass
          ? 'PASS'
          : 'RETRY';
    return {
      ...parsed.data,
      decision,
      teacherReason:
        decision === 'RETRY'
          ? (backgroundVetoMessage ??
            (thresholdDowngraded
              ? lowConfidenceTeacherMessage
              : parsed.data.teacherReason))
          : parsed.data.teacherReason,
      confidenceSummary: {
        ...parsed.data.confidenceSummary,
        minimumRequired: minimumConfidence,
        minimumRequiredByCriterion: usesTeachingEnvironmentProfile
          ? Object.fromEntries(
              criteriaKeys.map((criterionKey) => [
                criterionKey,
                teacherPhotoMinimumConfidence(criterionKey),
              ]),
            )
          : undefined,
        minimumObserved:
          confidences.length > 0 ? Math.min(...confidences) : null,
        criteria: Object.fromEntries(
          criteria.map((item) => [item.criterionKey, item.confidence ?? null]),
        ),
        centralExposure: exposure,
        centralExposureIssue: exposureIssue,
      },
      criteria,
    };
  }

  private async handleTechnicalError(
    context: TaskRuleContext,
    fileId: string,
    criteriaVersion: string,
    criteriaKeys: string[],
    aiRunId: string | null,
    resultCode: string,
    isEphemeralPersonalizedReview: boolean,
  ): Promise<TaskRuleResult> {
    if (isEphemeralPersonalizedReview) {
      return {
        passed: false,
        resultCode,
        teacherMessage: ephemeralTechnicalTeacherMessage,
      };
    }
    const items: ImageReviewItem[] = criteriaKeys.map((criterionKey) => ({
      criterionKey,
      result: 'UNKNOWN',
      teacherMessage: technicalTeacherMessage,
    }));
    await this.reviews.save(context.client, {
      fileId,
      submissionId: context.submissionId,
      aiRunId,
      criteriaVersion,
      decision: 'ERROR',
      teacherReason: technicalTeacherMessage,
      confidenceSummary: { errorCode: resultCode },
      items,
    });
    return this.deferred(resultCode);
  }

  private deferred(
    resultCode: string,
    teacherMessage = technicalTeacherMessage,
  ): TaskRuleResult {
    return { passed: false, deferred: true, resultCode, teacherMessage };
  }
}
