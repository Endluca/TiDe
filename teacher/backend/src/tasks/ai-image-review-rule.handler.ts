import { Injectable } from '@nestjs/common';
import { z } from 'zod';
import { FileStorageAdapter } from '../files/file-storage.adapter';
import { AiGatewayService } from '../integrations/ai/ai-gateway.service';
import { TaskRuleHandler } from './task-rule.handler';
import {
  ImageReviewRepository,
  type ImageReviewItem,
} from './image-review.repository';
import type { TaskRuleContext, TaskRuleResult } from './task-validation.models';

const ruleConfigSchema = z
  .object({
    stepKey: z.string().min(1).max(128),
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
          })
          .strict(),
      )
      .min(1)
      .max(50),
  })
  .strict();

const technicalTeacherMessage =
  '暂时无法完成自动检查，本次提交已转为人工复核。';

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
    if (
      new Set(config.criteriaKeys).size !== config.criteriaKeys.length ||
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

    let content: Buffer;
    try {
      content = await this.storage.read(file.objectKey, file.storageProvider);
    } catch {
      return this.saveTechnicalError(
        context,
        fileId,
        config.criteriaVersion,
        config.criteriaKeys,
        null,
        'IMAGE_REVIEW_FILE_UNAVAILABLE',
      );
    }
    const execution = await this.gateway.execute({
      capability: 'TASK_IMAGE_REVIEW',
      callerModule: 'TASK_VALIDATION',
      promptVersionId: config.promptVersionId ?? null,
      systemPrompt: config.systemPrompt,
      userText: config.userText,
      file: {
        content,
        filename: file.originalFilename,
        mimeType: file.mimeType,
      },
    });
    if (execution.status === 'FAILED') {
      return this.saveTechnicalError(
        context,
        fileId,
        config.criteriaVersion,
        config.criteriaKeys,
        execution.aiRunId,
        execution.errorCode,
      );
    }

    const parsedReview = this.parseReview(
      execution.content,
      config.criteriaKeys,
    );
    if (!parsedReview) {
      return this.saveTechnicalError(
        context,
        fileId,
        config.criteriaVersion,
        config.criteriaKeys,
        execution.aiRunId,
        'IMAGE_REVIEW_RESPONSE_INVALID',
      );
    }
    await this.reviews.save(context.client, {
      fileId,
      submissionId: context.submissionId,
      aiRunId: execution.aiRunId,
      criteriaVersion: config.criteriaVersion,
      decision: parsedReview.decision,
      teacherReason: parsedReview.teacherReason,
      confidenceSummary: parsedReview.confidenceSummary,
      items: parsedReview.criteria,
    });
    if (parsedReview.decision === 'ERROR') {
      return this.deferred('IMAGE_REVIEW_ERROR', parsedReview.teacherReason);
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
  ): z.infer<typeof reviewResponseSchema> | null {
    let json: unknown;
    try {
      json = JSON.parse(content);
    } catch {
      return null;
    }
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
    const allCriteriaPass = parsed.data.criteria.every(
      (item) => item.result === 'PASS',
    );
    return {
      ...parsed.data,
      decision:
        parsed.data.decision === 'ERROR'
          ? 'ERROR'
          : parsed.data.decision === 'PASS' && allCriteriaPass
            ? 'PASS'
            : 'RETRY',
    };
  }

  private async saveTechnicalError(
    context: TaskRuleContext,
    fileId: string,
    criteriaVersion: string,
    criteriaKeys: string[],
    aiRunId: string | null,
    resultCode: string,
  ): Promise<TaskRuleResult> {
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
