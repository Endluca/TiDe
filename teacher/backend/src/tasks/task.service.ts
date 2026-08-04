import {
  BadRequestException,
  ConflictException,
  Injectable,
  NotFoundException,
  Optional,
  UnprocessableEntityException,
} from '@nestjs/common';
import { createHash } from 'node:crypto';
import { AppEventService } from '../app-events/app-event.service';
import type { AuthPrincipal } from '../auth/auth.models';
import type { TaskContext, TaskListResponse } from './task.models';
import type {
  TaskMutationResponse,
  TaskValidationResponse,
} from './task.models';
import type { MutationMetaDto } from './dto/mutation-meta.dto';
import type { RetryTaskDto } from './dto/retry-task.dto';
import type { SaveProgressDto } from './dto/save-progress.dto';
import type { SubmitTaskDto } from './dto/submit-task.dto';
import type { VideoHeartbeatDto } from './dto/video-heartbeat.dto';
import type { RefreshKuozhiProgressDto } from './dto/refresh-kuozhi-progress.dto';
import {
  OwnedTaskNotFoundError,
  TaskCommandConflictError,
  TaskRepository,
} from './task.repository';
import { KuozhiService } from '../integrations/kuozhi/kuozhi.service';
import type {
  KuozhiLaunchResponse,
  KuozhiProgressResponse,
} from '../integrations/kuozhi/kuozhi.models';
import {
  KuozhiOwnedTaskNotFoundError,
  KuozhiProgressConflictError,
  KuozhiProgressRepository,
} from '../integrations/kuozhi/kuozhi-progress.repository';

@Injectable()
export class TaskService {
  constructor(
    private readonly repository: TaskRepository,
    @Optional() private readonly events?: AppEventService,
    @Optional() private readonly kuozhi?: KuozhiService,
    @Optional() private readonly kuozhiProgress?: KuozhiProgressRepository,
  ) {}

  async list(principal: AuthPrincipal): Promise<TaskListResponse> {
    const binding = await this.repository.findBinding(principal.accountId);
    if (!binding) {
      throw this.notFound();
    }

    return this.repository.listTasksWithContexts(binding.teacherId);
  }

  async get(
    principal: AuthPrincipal,
    taskInstanceId: string,
  ): Promise<TaskContext> {
    const task = await this.repository.findTask(
      principal.accountId,
      taskInstanceId,
    );
    if (!task) {
      throw this.notFound();
    }
    return task;
  }

  async getKuozhiLaunch(
    principal: AuthPrincipal,
    taskInstanceId: string,
  ): Promise<KuozhiLaunchResponse> {
    const [task, binding] = await Promise.all([
      this.repository.findTask(principal.accountId, taskInstanceId),
      this.repository.findBinding(principal.accountId),
    ]);
    if (!task || !binding) {
      throw this.notFound();
    }
    if (!this.kuozhi) {
      throw new UnprocessableEntityException({
        code: 'KUOZHI_INTEGRATION_UNAVAILABLE',
        message: '课程服务暂不可用，请稍后重试',
        retryable: true,
      });
    }
    return this.kuozhi.createLaunch(task.taskCode, binding.teacherId);
  }

  async getKuozhiProgress(
    principal: AuthPrincipal,
    taskInstanceId: string,
  ): Promise<KuozhiProgressResponse> {
    const { task, resolved } = await this.kuozhiContext(
      principal,
      taskInstanceId,
    );
    const latest =
      resolved.dataMode === 'REAL' && this.kuozhiProgress
        ? await this.kuozhiProgress.getLatest(
            principal.accountId,
            taskInstanceId,
          )
        : null;
    return {
      ...(latest ?? this.kuozhi!.emptyProgress(resolved)),
      assignment: {
        status: task.status,
        stateVersion: task.stateVersion,
        stateUpdated: false,
      },
    };
  }

  async refreshKuozhiProgress(
    principal: AuthPrincipal,
    taskInstanceId: string,
    idempotencyKey: string | undefined,
    input: RefreshKuozhiProgressDto,
  ): Promise<KuozhiProgressResponse> {
    const { task, resolved } = await this.kuozhiContext(
      principal,
      taskInstanceId,
    );
    const command = this.commandInput(
      principal,
      taskInstanceId,
      idempotencyKey,
      'KUOZHI_PROGRESS_REFRESH',
      input,
    );
    const references = this.kuozhi!.passScoreReferences(resolved.mapping);
    const passScores =
      references.length > 0
        ? await this.requiredKuozhiProgress().loadPublishedPassScores(
            references,
          )
        : new Map<string, number>();
    const progress = await this.kuozhi!.fetchProgress(resolved, passScores);

    if (resolved.dataMode === 'SAMPLE_DRY_RUN') {
      return {
        ...progress,
        assignment: {
          status: task.status,
          stateVersion: task.stateVersion,
          stateUpdated: false,
        },
      };
    }

    try {
      return await this.requiredKuozhiProgress().persistRefresh({
        ...command,
        expectedStateVersion: input.expectedStateVersion,
        progress,
      });
    } catch (error) {
      if (error instanceof KuozhiOwnedTaskNotFoundError) throw this.notFound();
      if (error instanceof KuozhiProgressConflictError) {
        throw new ConflictException({
          code: error.reason,
          message:
            error.reason === 'STATE_VERSION_CONFLICT'
              ? '任务状态已更新，请重新读取后再刷新'
              : '幂等键或命令编号已用于其他请求',
          retryable: error.reason === 'STATE_VERSION_CONFLICT',
          ...(Object.keys(error.details).length > 0
            ? { details: error.details }
            : {}),
        });
      }
      throw error;
    }
  }

  async start(
    principal: AuthPrincipal,
    taskInstanceId: string,
    idempotencyKey: string | undefined,
    input: MutationMetaDto,
    analyticsSessionId?: string,
  ): Promise<TaskMutationResponse> {
    const response = await this.executeCommand(() =>
      this.repository.startTask({
        ...this.commandInput(
          principal,
          taskInstanceId,
          idempotencyKey,
          'START',
          input,
        ),
        expectedStateVersion: input.expectedStateVersion,
      }),
    );
    this.captureTaskEvent(
      principal,
      taskInstanceId,
      'TASK_STARTED',
      input.commandId,
      { result: 'SUCCESS' },
      analyticsSessionId,
    );
    return response;
  }

  async view(
    principal: AuthPrincipal,
    taskInstanceId: string,
    idempotencyKey: string | undefined,
    input: MutationMetaDto,
  ): Promise<TaskMutationResponse> {
    const response = await this.executeCommand(() =>
      this.repository.viewTask({
        ...this.commandInput(
          principal,
          taskInstanceId,
          idempotencyKey,
          'VIEW',
          input,
        ),
        expectedStateVersion: input.expectedStateVersion,
      }),
    );
    return response;
  }

  async saveProgress(
    principal: AuthPrincipal,
    taskInstanceId: string,
    idempotencyKey: string | undefined,
    input: SaveProgressDto,
    analyticsSessionId?: string,
  ): Promise<TaskMutationResponse> {
    const response = await this.executeCommand(() =>
      this.repository.saveProgress({
        ...this.commandInput(
          principal,
          taskInstanceId,
          idempotencyKey,
          'PROGRESS',
          input,
        ),
        expectedStateVersion: input.expectedStateVersion,
        stepKey: input.stepKey,
        percent: input.percent,
        progress: input.progress,
      }),
    );
    if (response.step?.status === 'COMPLETED') {
      this.captureTaskEvent(
        principal,
        taskInstanceId,
        'TASK_STEP_COMPLETED',
        input.commandId,
        {
          result: 'SUCCESS',
          stepKey: input.stepKey,
          percent: response.step.percent,
        },
        analyticsSessionId,
      );
    } else if (response.step?.status === 'FAILED') {
      this.captureTaskEvent(
        principal,
        taskInstanceId,
        'TASK_STEP_FAILED',
        input.commandId,
        {
          result: 'FAILURE',
          stepKey: input.stepKey,
          percent: response.step.percent,
        },
        analyticsSessionId,
      );
    }
    return response;
  }

  async saveVideoHeartbeat(
    principal: AuthPrincipal,
    taskInstanceId: string,
    input: VideoHeartbeatDto,
    analyticsSessionId?: string,
  ): Promise<TaskMutationResponse> {
    const response = await this.executeCommand(() =>
      this.repository.saveVideoHeartbeat({
        accountId: principal.accountId,
        taskInstanceId,
        stepKey: input.stepKey,
        positionSeconds: input.positionSeconds,
        playbackRate: input.playbackRate,
      }),
    );
    if (response.step?.status === 'COMPLETED') {
      const idBase = `${taskInstanceId}:${input.stepKey}:first-full-watch`;
      this.captureTaskEvent(
        principal,
        taskInstanceId,
        'VIDEO_COMPLETED',
        idBase,
        {
          result: 'SUCCESS',
          stepKey: input.stepKey,
          percent: 100,
          videoPositionSeconds: input.positionSeconds,
        },
        analyticsSessionId,
      );
      this.captureTaskEvent(
        principal,
        taskInstanceId,
        'TASK_STEP_COMPLETED',
        `${idBase}:step`,
        {
          result: 'SUCCESS',
          stepKey: input.stepKey,
          percent: 100,
        },
        analyticsSessionId,
      );
    }
    return response;
  }

  async submit(
    principal: AuthPrincipal,
    taskInstanceId: string,
    idempotencyKey: string | undefined,
    input: SubmitTaskDto,
    analyticsSessionId?: string,
  ): Promise<TaskMutationResponse> {
    const response = await this.executeCommand(() =>
      this.repository.submitTask({
        ...this.commandInput(
          principal,
          taskInstanceId,
          idempotencyKey,
          'SUBMIT',
          input,
        ),
        expectedStateVersion: input.expectedStateVersion,
        attemptId: input.attemptId,
        outputs: input.outputs,
      }),
    );
    this.captureTaskEvent(
      principal,
      taskInstanceId,
      'TASK_SUBMITTED',
      input.commandId,
      { result: 'SUCCESS' },
      analyticsSessionId,
    );
    this.captureTaskEvent(
      principal,
      taskInstanceId,
      'TASK_VALIDATION_STARTED',
      `${input.commandId}:validation`,
      { result: 'STARTED' },
      analyticsSessionId,
    );
    if (response.validation?.status === 'PASSED') {
      this.captureTaskEvent(
        principal,
        taskInstanceId,
        'TASK_VALIDATION_PASSED',
        `${input.commandId}:validation-result`,
        { result: 'PASSED' },
        analyticsSessionId,
      );
      this.captureTaskEvent(
        principal,
        taskInstanceId,
        'TASK_COMPLETED',
        `${input.commandId}:completed`,
        { result: 'SUCCESS' },
        analyticsSessionId,
      );
    } else if (response.validation?.status === 'FAILED') {
      void this.captureValidationFailure(
        principal,
        taskInstanceId,
        `${input.commandId}:validation-result`,
        response.validation.resultCode ?? 'VALIDATION_FAILED',
        analyticsSessionId,
      );
    } else if (response.validation?.status === 'ERROR') {
      this.captureTaskEvent(
        principal,
        taskInstanceId,
        'TASK_VALIDATION_FAILED',
        `${input.commandId}:validation-result`,
        {
          result: 'FAILURE',
          errorCode: response.validation.resultCode ?? 'VALIDATION_ERROR',
        },
        analyticsSessionId,
      );
    }
    return response;
  }

  async retry(
    principal: AuthPrincipal,
    taskInstanceId: string,
    idempotencyKey: string | undefined,
    input: RetryTaskDto,
    analyticsSessionId?: string,
  ): Promise<TaskMutationResponse> {
    const response = await this.executeCommand(() =>
      this.repository.retryTask({
        ...this.commandInput(
          principal,
          taskInstanceId,
          idempotencyKey,
          'RETRY',
          input,
        ),
        expectedStateVersion: input.expectedStateVersion,
        reasonCode: input.reasonCode,
      }),
    );
    this.captureTaskEvent(
      principal,
      taskInstanceId,
      'TASK_RETRY_STARTED',
      input.commandId,
      {
        result: 'SUCCESS',
        reasonCode: input.reasonCode ?? 'TEACHER_RETRY',
      },
      analyticsSessionId,
    );
    return response;
  }

  async getValidation(
    principal: AuthPrincipal,
    taskInstanceId: string,
  ): Promise<TaskValidationResponse> {
    const validation = await this.repository.getLatestValidation(
      principal.accountId,
      taskInstanceId,
    );
    if (!validation) {
      throw new NotFoundException({
        code: 'VALIDATION_NOT_FOUND',
        message: '该任务还没有提交验证结果',
        retryable: false,
      });
    }
    return validation;
  }

  private commandInput(
    principal: AuthPrincipal,
    taskInstanceId: string,
    idempotencyKey: string | undefined,
    operation: string,
    input: MutationMetaDto,
  ) {
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
    return {
      accountId: principal.accountId,
      taskInstanceId,
      idempotencyKey,
      commandId: input.commandId,
      requestHash: createHash('sha256')
        .update(
          JSON.stringify(
            this.stableValue({ operation, taskInstanceId, input }),
          ),
        )
        .digest('hex'),
    };
  }

  private async kuozhiContext(
    principal: AuthPrincipal,
    taskInstanceId: string,
  ) {
    const [task, binding] = await Promise.all([
      this.repository.findTask(principal.accountId, taskInstanceId),
      this.repository.findBinding(principal.accountId),
    ]);
    if (!task || !binding) throw this.notFound();
    if (!this.kuozhi) {
      throw new UnprocessableEntityException({
        code: 'KUOZHI_INTEGRATION_UNAVAILABLE',
        message: '课程服务暂不可用，请稍后重试',
        retryable: true,
      });
    }
    const resolved = await this.kuozhi.resolveMapping(
      task.taskCode,
      binding.teacherId,
    );
    return { task, resolved };
  }

  private requiredKuozhiProgress(): KuozhiProgressRepository {
    if (!this.kuozhiProgress) {
      throw new UnprocessableEntityException({
        code: 'KUOZHI_PROGRESS_UNAVAILABLE',
        message: '课程进度同步暂不可用，请稍后重试',
        retryable: true,
      });
    }
    return this.kuozhiProgress;
  }

  private async captureValidationFailure(
    principal: AuthPrincipal,
    taskInstanceId: string,
    eventId: string,
    errorCode: string,
    analyticsSessionId?: string,
  ): Promise<void> {
    try {
      const retryAllowed = await this.repository.isRetryAllowed(
        principal.accountId,
        taskInstanceId,
      );
      this.captureTaskEvent(
        principal,
        taskInstanceId,
        retryAllowed
          ? 'TASK_VALIDATION_RETRY_REQUIRED'
          : 'TASK_VALIDATION_FAILED',
        eventId,
        {
          result: retryAllowed ? 'RETRY_REQUIRED' : 'FINAL_FAILURE',
          errorCode,
        },
        analyticsSessionId,
      );
    } catch {
      // Analytics enrichment must not change an already committed submission.
    }
  }

  private stableValue(value: unknown): unknown {
    if (Array.isArray(value)) {
      return value.map((item) => this.stableValue(item));
    }
    if (typeof value === 'object' && value !== null) {
      return Object.fromEntries(
        Object.entries(value)
          .sort(([left], [right]) => left.localeCompare(right))
          .map(([key, child]) => [key, this.stableValue(child)]),
      );
    }
    return value;
  }

  private async executeCommand(
    command: () => Promise<TaskMutationResponse>,
  ): Promise<TaskMutationResponse> {
    try {
      return await command();
    } catch (error) {
      if (error instanceof OwnedTaskNotFoundError) {
        throw this.notFound();
      }
      if (error instanceof TaskCommandConflictError) {
        const payload = {
          code: error.reason,
          message: this.commandErrorMessage(error.reason),
          retryable:
            error.reason === 'STATE_VERSION_CONFLICT' ||
            error.reason === 'FILE_NOT_READY',
          ...(Object.keys(error.details).length > 0
            ? { details: error.details }
            : {}),
        };
        if (
          error.reason === 'STEP_NOT_FOUND' ||
          error.reason === 'OUTPUT_INVALID' ||
          error.reason === 'CONTENT_NOT_READY' ||
          error.reason === 'PREVIOUS_STEP_INCOMPLETE'
        ) {
          throw new UnprocessableEntityException(payload);
        }
        throw new ConflictException(payload);
      }
      throw error;
    }
  }

  private captureTaskEvent(
    principal: AuthPrincipal,
    taskAssignmentId: string,
    eventName: string,
    idempotencySeed: string,
    properties: Record<string, unknown>,
    analyticsSessionId?: string,
  ): void {
    this.events?.captureSystem({
      eventName,
      eventId: `server-event-${createHash('sha256')
        .update(`${eventName}:${taskAssignmentId}:${idempotencySeed}`)
        .digest('hex')}`,
      accountId: principal.accountId,
      sessionId:
        analyticsSessionId &&
        analyticsSessionId.length >= 8 &&
        analyticsSessionId.length <= 128
          ? analyticsSessionId
          : principal.sessionId,
      taskAssignmentId,
      properties,
    });
  }

  private commandErrorMessage(reason: string): string {
    const messages: Record<string, string> = {
      IDEMPOTENCY_CONFLICT: '幂等键或命令编号已用于其他请求',
      STATE_VERSION_CONFLICT: '任务状态已更新，请重新读取后再操作',
      INVALID_STATE_TRANSITION: '当前任务状态不允许该操作',
      STEP_NOT_FOUND: '任务步骤不存在',
      OUTPUT_INVALID: '提交结果与任务步骤不匹配',
      ATTEMPT_CONFLICT: '本次尝试编号已被使用',
      FILE_NOT_READY: '提交的文件尚未完成校验',
      CONTENT_NOT_READY: '任务正式内容尚未发布',
      PREVIOUS_STEP_INCOMPLETE: '请先完成前一个步骤',
    };
    return messages[reason] ?? '任务操作冲突';
  }

  private notFound(): NotFoundException {
    return new NotFoundException({
      code: 'TASK_NOT_FOUND',
      message: '未找到该任务',
      retryable: false,
    });
  }
}
