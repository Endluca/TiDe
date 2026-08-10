import { Injectable } from '@nestjs/common';
import { randomUUID } from 'node:crypto';
import type { PoolClient, QueryResultRow } from 'pg';
import { buildTaskResultNotificationContent } from '../notifications/task-result-notification.policy';
import { DatabaseService } from '../platform/database/database.service';
import type { StepOutputDto } from './dto/submit-task.dto';
import type {
  TaskContext,
  TaskKind,
  TaskListResponse,
  TaskMutationResponse,
  TaskRelatedCourse,
  TaskStatus,
  TaskStepType,
  TaskSummary,
  TaskTeacherSafeFact,
  TaskValidationResponse,
} from './task.models';
import { TaskValidationEngine } from './task-validation.engine';
import type {
  TaskValidationRule,
  TaskValidationStep,
} from './task-validation.models';

export type TaskCommandConflictReason =
  | 'IDEMPOTENCY_CONFLICT'
  | 'STATE_VERSION_CONFLICT'
  | 'INVALID_STATE_TRANSITION'
  | 'STEP_NOT_FOUND'
  | 'OUTPUT_INVALID'
  | 'ATTEMPT_CONFLICT'
  | 'FILE_NOT_READY'
  | 'CONTENT_NOT_READY'
  | 'PREVIOUS_STEP_INCOMPLETE';

export class TaskCommandConflictError extends Error {
  constructor(
    public readonly reason: TaskCommandConflictReason,
    public readonly details: Record<string, unknown> = {},
  ) {
    super(reason);
    this.name = 'TaskCommandConflictError';
  }
}

export class OwnedTaskNotFoundError extends Error {
  constructor() {
    super('Owned task was not found');
    this.name = 'OwnedTaskNotFoundError';
  }
}

export interface TeacherTaskBinding {
  bindingId: string;
  teacherId: string;
}

interface BindingRow extends QueryResultRow, TeacherTaskBinding {}

interface TaskRow extends QueryResultRow {
  taskInstanceId: string;
  taskCode: string;
  kind: TaskKind;
  status: TaskStatus;
  stateVersion: string;
  templateVersion: number;
  executionContractVersion: string;
  language: string;
  title: string;
  why: string;
  whatToDo: string;
  completionStandard: string;
  outcome: string;
  contentConfig: Record<string, unknown>;
  priority: 'P0' | 'P1' | 'P2' | 'P3';
  assignmentId: string;
  teacherSafeReason: string;
  evidenceSnapshot: unknown;
  teacherSafeFacts: unknown;
  relatedCourses: unknown;
  reminderNotificationId: string | null;
  availableAt: Date | null;
  dueAt: Date | null;
  completedAt: Date | null;
  dataOrigin:
    'REAL' | 'DERIVED_REAL' | 'MOCK' | 'MOCK_SIMULATION' | 'MOCK_PROXY';
}

interface StepRow extends QueryResultRow {
  stepKey: string;
  position: number;
  type: TaskStepType;
  title: string;
  config: Record<string, unknown>;
  progressStatus: 'NOT_STARTED' | 'IN_PROGRESS' | 'COMPLETED' | 'FAILED';
  progressPercent: number;
  progressSummary: Record<string, unknown>;
}

interface BatchStepRow extends StepRow {
  taskInstanceId: string;
}

interface CommandReceiptRow extends QueryResultRow {
  taskAssignmentId: string;
  idempotencyKey: string;
  commandId: string;
  requestHash: string;
  responseBody: TaskMutationResponse;
}

interface LockedTaskRow extends QueryResultRow {
  taskInstanceId: string;
  status: TaskStatus;
  stateVersion: string;
  executionVersionId: string;
  contentConfig: Record<string, unknown>;
  sourceType: TaskKind;
  taskCode: string;
  taskTitle: string;
  dataOrigin:
    'REAL' | 'DERIVED_REAL' | 'MOCK' | 'MOCK_SIMULATION' | 'MOCK_PROXY';
  teacherId: string;
  assignmentId: string;
}

interface StepDefinitionRow extends QueryResultRow {
  stepKey: string;
  stepType: TaskStepType;
  config: Record<string, unknown>;
}

interface VideoProgressRow extends QueryResultRow {
  durationSeconds: number;
  resumeSeconds: number;
  maxContiguousSeconds: number;
  firstFullWatchAt: Date | null;
  updatedAt: Date;
}

interface EvaluatedStepProgress {
  status: 'NOT_STARTED' | 'IN_PROGRESS' | 'COMPLETED' | 'FAILED';
  percent: number;
  summary: Record<string, unknown>;
  result?: Record<string, unknown>;
}

interface StepEvidenceRow extends QueryResultRow {
  stepKey: string;
  stepType: TaskStepType;
  config: Record<string, unknown>;
  progressSummary: Record<string, unknown>;
}

interface ValidationRuleRow extends QueryResultRow, TaskValidationRule {}

interface ValidationStepRow extends QueryResultRow, TaskValidationStep {}

interface ValidationRow extends QueryResultRow {
  status: TaskValidationResponse['status'];
  resultCode: string | null;
  teacherMessage: string | null;
  imageReview: TaskValidationResponse['imageReview'];
}

type TaskCommandType = 'VIEW' | 'START' | 'PROGRESS' | 'SUBMIT' | 'RETRY';

interface TaskCommandInput {
  accountId: string;
  taskInstanceId: string;
  idempotencyKey: string;
  commandId: string;
  requestHash: string;
  expectedStateVersion: number;
}

interface VideoHeartbeatInput {
  accountId: string;
  taskInstanceId: string;
  stepKey: string;
  positionSeconds: number;
  playbackRate: number;
}

@Injectable()
export class TaskRepository {
  constructor(
    private readonly database: DatabaseService,
    private readonly validationEngine: TaskValidationEngine,
  ) {}

  async findBinding(accountId: string): Promise<TeacherTaskBinding | null> {
    const result = await this.database.queryTide<BindingRow>(
      `
        SELECT id AS "bindingId", teacher_id AS "teacherId"
        FROM tide.teacher_bindings
        WHERE account_id = $1 AND status = 'ACTIVE'
        LIMIT 1
      `,
      [accountId],
    );
    return result.rows[0] ?? null;
  }

  async listTasks(teacherId: string): Promise<TaskSummary[]> {
    const rows = await this.listTaskRows(teacherId);
    return rows.map((row) => this.toTaskSummary(row));
  }

  async listTasksWithContexts(teacherId: string): Promise<TaskListResponse> {
    const tasks = await this.listTaskRows(teacherId);
    if (tasks.length === 0) {
      return { items: [], contexts: [] };
    }

    const taskInstanceIds = tasks.map((task) => task.taskInstanceId);
    const stepResult = await this.database.queryTide<BatchStepRow>(
      `
        SELECT
          assignment.assignment_id AS "taskInstanceId",
          definition.step_key AS "stepKey",
          definition.position,
          definition.step_type AS type,
          definition.title,
          definition.config,
          COALESCE(progress.status, 'NOT_STARTED') AS "progressStatus",
          COALESCE(progress.percent, 0) AS "progressPercent",
          COALESCE(progress.progress_summary, '{}'::jsonb) AS "progressSummary"
        FROM public.task_assignments assignment
        JOIN tide.task_execution_versions execution
          ON execution.shared_template_row_id = assignment.template_version_id
        JOIN tide.task_step_definitions definition
          ON definition.execution_version_id = execution.id
        LEFT JOIN tide.task_step_progress progress
          ON progress.task_assignment_id = assignment.assignment_id
         AND progress.step_key = definition.step_key
        WHERE assignment.assignment_id = ANY($1::varchar[])
        ORDER BY assignment.assignment_id, definition.position
      `,
      [taskInstanceIds],
    );
    const stepsByTask = new Map<string, BatchStepRow[]>();
    for (const step of stepResult.rows) {
      const current = stepsByTask.get(step.taskInstanceId) ?? [];
      current.push(step);
      stepsByTask.set(step.taskInstanceId, current);
    }

    return {
      items: tasks.map((task) => this.toTaskSummary(task)),
      contexts: tasks.map((task) =>
        this.toTaskContext(task, stepsByTask.get(task.taskInstanceId) ?? []),
      ),
    };
  }

  async findTask(
    accountId: string,
    taskInstanceId: string,
  ): Promise<TaskContext | null> {
    const result = await this.database.queryTide<TaskRow>(
      `${this.taskSelect()}
       JOIN tide.teacher_bindings owner ON owner.teacher_id = task.teacher_id
       WHERE task.assignment_id = $1
         AND owner.account_id = $2
         AND owner.status = 'ACTIVE'
         AND ${this.stageAvailabilityCondition()}
       LIMIT 1`,
      [taskInstanceId, accountId],
    );
    const task = result.rows[0];
    if (!task) {
      return null;
    }

    const steps = await this.database.queryTide<StepRow>(
      `
        SELECT
          definition.step_key AS "stepKey",
          definition.position,
          definition.step_type AS type,
          definition.title,
          definition.config,
          COALESCE(progress.status, 'NOT_STARTED') AS "progressStatus",
          COALESCE(progress.percent, 0) AS "progressPercent",
          COALESCE(progress.progress_summary, '{}'::jsonb) AS "progressSummary"
        FROM tide.task_step_definitions definition
        LEFT JOIN tide.task_step_progress progress
          ON progress.task_assignment_id = $1
         AND progress.step_key = definition.step_key
        WHERE definition.execution_version_id = (
          SELECT execution.id
          FROM public.task_assignments assignment
          JOIN tide.task_execution_versions execution
            ON execution.shared_template_row_id = assignment.template_version_id
          WHERE assignment.assignment_id = $1
        )
        ORDER BY definition.position
      `,
      [taskInstanceId],
    );
    return this.toTaskContext(task, steps.rows);
  }

  startTask(input: TaskCommandInput): Promise<TaskMutationResponse> {
    return this.database.withTideTransaction(async (client) => {
      const replay = await this.findCommandReplay(client, input);
      if (replay) {
        return replay;
      }
      const task = await this.lockOwnedTask(client, input);
      this.assertStateVersion(task, input.expectedStateVersion);
      this.assertStatus(task, ['ASSIGNED', 'VIEWED']);
      this.assertContentReady(task);
      const stateVersion = await this.updateAssignmentStatus(
        client,
        input.taskInstanceId,
        input.expectedStateVersion,
        'IN_PROGRESS',
        null,
      );
      const response = this.mutationResponse(
        input.taskInstanceId,
        'IN_PROGRESS',
        stateVersion,
      );
      await this.saveCommandReceipt(client, input, 'START', response);
      return response;
    });
  }

  viewTask(input: TaskCommandInput): Promise<TaskMutationResponse> {
    return this.database.withTideTransaction(async (client) => {
      const replay = await this.findCommandReplay(client, input);
      if (replay) {
        return replay;
      }
      const task = await this.lockOwnedTask(client, input);
      this.assertStateVersion(task, input.expectedStateVersion);
      this.assertStatus(task, ['ASSIGNED']);
      const stateVersion = await this.updateAssignmentStatus(
        client,
        input.taskInstanceId,
        input.expectedStateVersion,
        'VIEWED',
        null,
      );
      const response = this.mutationResponse(
        input.taskInstanceId,
        'VIEWED',
        stateVersion,
      );
      await this.saveCommandReceipt(client, input, 'VIEW', response);
      return response;
    });
  }

  saveProgress(
    input: TaskCommandInput & {
      stepKey: string;
      percent: number;
      progress: Record<string, unknown>;
    },
  ): Promise<TaskMutationResponse> {
    return this.database.withTideTransaction(async (client) => {
      const replay = await this.findCommandReplay(client, input);
      if (replay) {
        return replay;
      }
      const task = await this.lockOwnedTask(client, input);
      this.assertStateVersion(task, input.expectedStateVersion);
      this.assertStatus(task, ['IN_PROGRESS']);
      const step = await client.query<StepDefinitionRow>(
        `
          SELECT step_key AS "stepKey", step_type AS "stepType", config
          FROM tide.task_step_definitions
          WHERE execution_version_id = $1 AND step_key = $2
          LIMIT 1
        `,
        [task.executionVersionId, input.stepKey],
      );
      if (step.rowCount === 0) {
        throw new TaskCommandConflictError('STEP_NOT_FOUND', {
          stepKey: input.stepKey,
        });
      }
      await this.assertPreviousStepsComplete(client, task, input.stepKey);

      const evaluated = await this.evaluateStepProgress(
        client,
        task,
        step.rows[0],
        input.progress,
        input.percent,
        input.accountId,
      );
      await this.persistStepProgress(
        client,
        input.taskInstanceId,
        input.stepKey,
        evaluated,
      );
      const response = this.mutationResponse(
        input.taskInstanceId,
        'IN_PROGRESS',
        Number(task.stateVersion),
        undefined,
        {
          stepKey: input.stepKey,
          status: evaluated.status,
          percent: evaluated.percent,
          ...(evaluated.result ? { result: evaluated.result } : {}),
          details: evaluated.summary,
        },
      );
      await this.saveCommandReceipt(client, input, 'PROGRESS', response);
      return response;
    });
  }

  saveVideoHeartbeat(
    input: VideoHeartbeatInput,
  ): Promise<TaskMutationResponse> {
    return this.database.withTideTransaction(async (client) => {
      const task = await this.lockOwnedTask(client, input);
      this.assertStatus(task, ['IN_PROGRESS']);
      const step = await client.query<StepDefinitionRow>(
        `
          SELECT step_key AS "stepKey", step_type AS "stepType", config
          FROM tide.task_step_definitions
          WHERE execution_version_id = $1 AND step_key = $2
          LIMIT 1
        `,
        [task.executionVersionId, input.stepKey],
      );
      const definition = step.rows[0];
      if (!definition) {
        throw new TaskCommandConflictError('STEP_NOT_FOUND', {
          stepKey: input.stepKey,
        });
      }
      if (definition.stepType !== 'VIDEO') {
        throw new TaskCommandConflictError('OUTPUT_INVALID', {
          stepKey: input.stepKey,
        });
      }
      await this.assertPreviousStepsComplete(client, task, input.stepKey);

      const evaluated = await this.evaluateVideoProgress(
        client,
        task,
        definition,
        {
          positionSeconds: input.positionSeconds,
          playbackRate: input.playbackRate,
        },
      );
      await this.persistStepProgress(
        client,
        input.taskInstanceId,
        input.stepKey,
        evaluated,
      );
      return this.mutationResponse(
        input.taskInstanceId,
        'IN_PROGRESS',
        Number(task.stateVersion),
        undefined,
        {
          stepKey: input.stepKey,
          status: evaluated.status,
          percent: evaluated.percent,
          ...(evaluated.result ? { result: evaluated.result } : {}),
          details: evaluated.summary,
        },
      );
    });
  }

  submitTask(
    input: TaskCommandInput & {
      attemptId: string;
      outputs: StepOutputDto[];
    },
  ): Promise<TaskMutationResponse> {
    return this.database.withTideTransaction(async (client) => {
      const replay = await this.findCommandReplay(client, input);
      if (replay) {
        return replay;
      }
      const task = await this.lockOwnedTask(client, input);
      this.assertStateVersion(task, input.expectedStateVersion);
      this.assertStatus(task, ['IN_PROGRESS']);
      const definitions = await this.loadStepDefinitions(
        client,
        task.executionVersionId,
      );
      const outputs = await this.resolveSubmissionOutputs(
        client,
        input.taskInstanceId,
        definitions,
        input.outputs,
      );
      await this.validateOutputs(client, { ...input, outputs }, definitions);

      const attemptExists = await client.query(
        `SELECT 1 FROM tide.task_attempts WHERE id = $1 LIMIT 1`,
        [input.attemptId],
      );
      if (attemptExists.rowCount) {
        throw new TaskCommandConflictError('ATTEMPT_CONFLICT');
      }
      const attemptNumber = await client.query<{ attemptNo: number }>(
        `SELECT COALESCE(MAX(attempt_no), 0) + 1 AS "attemptNo" FROM tide.task_attempts WHERE task_assignment_id = $1`,
        [input.taskInstanceId],
      );
      const submissionId = randomUUID();
      await client.query(
        `
          INSERT INTO tide.task_attempts (
            id, task_assignment_id, attempt_no, status, submitted_at
          ) VALUES ($1, $2, $3, 'SUBMITTED', now())
        `,
        [
          input.attemptId,
          input.taskInstanceId,
          attemptNumber.rows[0].attemptNo,
        ],
      );
      await client.query(
        `
          INSERT INTO tide.task_submissions (
            id, task_assignment_id, task_attempt_id, submission_type,
            payload, validation_status, rule_version
          ) VALUES ($1, $2, $3, 'TASK', $4, 'PENDING', 'pending')
        `,
        [submissionId, input.taskInstanceId, input.attemptId, { outputs }],
      );
      await this.linkSubmissionFiles(client, submissionId, outputs);
      await this.persistProgressEvidence(
        client,
        input.attemptId,
        submissionId,
        input.taskInstanceId,
        definitions,
      );
      const submittedStateVersion = await this.updateAssignmentStatus(
        client,
        input.taskInstanceId,
        input.expectedStateVersion,
        'SUBMITTED',
        null,
      );
      const rules = await this.loadValidationRules(
        client,
        task.executionVersionId,
      );
      const progress = await this.loadValidationSteps(
        client,
        input.taskInstanceId,
        task.executionVersionId,
      );
      const decision = await this.validationEngine.evaluate({
        client,
        accountId: input.accountId,
        taskInstanceId: input.taskInstanceId,
        submissionId,
        rules,
        steps: progress,
        outputs,
      });
      const attemptStatus =
        decision.status === 'PASSED'
          ? 'PASSED'
          : decision.status === 'FAILED'
            ? 'FAILED'
            : 'SUBMITTED';
      await client.query(
        `
          UPDATE tide.task_attempts
          SET status = $2,
              ended_at = CASE WHEN $2 IN ('PASSED', 'FAILED') THEN now() ELSE NULL END,
              result_code = $3
          WHERE id = $1
        `,
        [input.attemptId, attemptStatus, decision.resultCode],
      );
      await client.query(
        `
          UPDATE tide.task_submissions
          SET payload = $2, validation_status = $3, result_code = $4,
              rule_version = $5,
              validated_at = CASE WHEN $3::text IN ('PASSED', 'FAILED', 'ERROR') THEN now() ELSE NULL END
          WHERE id = $1
        `,
        [
          submissionId,
          { outputs, teacherMessage: decision.teacherMessage },
          decision.status,
          decision.resultCode,
          decision.ruleVersion,
        ],
      );

      const nextStatus: TaskStatus =
        decision.status === 'PASSED'
          ? 'COMPLETED'
          : decision.status === 'FAILED'
            ? 'FAILED'
            : 'UNDER_REVIEW';
      const stateVersion = await this.updateAssignmentStatus(
        client,
        input.taskInstanceId,
        submittedStateVersion,
        nextStatus,
        nextStatus === 'FAILED'
          ? (decision.resultCode ?? 'VALIDATION_FAILED')
          : null,
      );
      await this.createTaskResultNotification(
        client,
        task,
        input.attemptId,
        decision.status,
        decision.ruleVersion,
      );
      if (decision.status === 'PASSED') {
        const completionSource = rules.some(
          (rule) => rule.ruleType === 'AI_IMAGE_REVIEW',
        )
          ? 'AI_REVIEW'
          : 'RULE_ENGINE';
        await client.query(
          `
            INSERT INTO tide.task_completions (
              id, task_assignment_id, task_submission_id,
              completion_source, result_version, trusted_at
            ) VALUES ($1, $2, $3, $4, $5, now())
          `,
          [
            randomUUID(),
            input.taskInstanceId,
            submissionId,
            completionSource,
            decision.ruleVersion,
          ],
        );
      }
      const response = this.mutationResponse(
        input.taskInstanceId,
        nextStatus,
        stateVersion,
        {
          status: decision.status,
          resultCode: decision.resultCode,
          teacherMessage: decision.teacherMessage,
        },
      );
      await this.saveCommandReceipt(client, input, 'SUBMIT', response);
      return response;
    });
  }

  retryTask(
    input: TaskCommandInput & { reasonCode: string | undefined },
  ): Promise<TaskMutationResponse> {
    return this.database.withTideTransaction(async (client) => {
      const replay = await this.findCommandReplay(client, input);
      if (replay) {
        return replay;
      }
      const task = await this.lockOwnedTask(client, input);
      this.assertStateVersion(task, input.expectedStateVersion);
      this.assertStatus(task, ['FAILED']);
      if (task.contentConfig.allowRetry !== true) {
        throw new TaskCommandConflictError('INVALID_STATE_TRANSITION', {
          currentStatus: task.status,
          retryConfigured: false,
        });
      }
      const stateVersion = await this.updateAssignmentStatus(
        client,
        input.taskInstanceId,
        input.expectedStateVersion,
        'IN_PROGRESS',
        null,
      );
      const response = this.mutationResponse(
        input.taskInstanceId,
        'IN_PROGRESS',
        stateVersion,
      );
      await this.saveCommandReceipt(client, input, 'RETRY', response);
      return response;
    });
  }

  async getLatestValidation(
    accountId: string,
    taskInstanceId: string,
  ): Promise<TaskValidationResponse | null> {
    const result = await this.database.queryTide<ValidationRow>(
      `
        SELECT
          submission.validation_status AS status,
          submission.result_code AS "resultCode",
          submission.payload->>'teacherMessage' AS "teacherMessage",
          CASE
            WHEN image_review.id IS NULL THEN NULL
            ELSE jsonb_build_object(
              'criteriaVersion', image_review.criteria_version,
              'decision', image_review.decision,
              'teacherReason', image_review.teacher_reason,
              'confidenceSummary', image_review.confidence_summary,
              'items', image_review.items
            )
          END AS "imageReview"
        FROM tide.task_submissions submission
        JOIN public.task_assignments task ON task.assignment_id = submission.task_assignment_id
        JOIN tide.teacher_bindings binding ON binding.teacher_id = task.teacher_id
        LEFT JOIN LATERAL (
          SELECT
            review.id,
            review.criteria_version,
            review.decision,
            review.teacher_reason,
            review.confidence_summary,
            COALESCE(
              (
                SELECT jsonb_agg(
                  jsonb_build_object(
                    'criterionKey', item.criterion_key,
                    'result', item.result,
                    'teacherMessage', item.teacher_message
                  )
                  ORDER BY item.criterion_key
                )
                FROM tide.image_review_items item
                WHERE item.image_review_id = review.id
              ),
              '[]'::jsonb
            ) AS items
          FROM tide.image_reviews review
          WHERE review.submission_id = submission.id
          ORDER BY review.reviewed_at DESC, review.id DESC
          LIMIT 1
        ) image_review ON TRUE
        WHERE submission.task_assignment_id = $1
          AND binding.account_id = $2
          AND binding.status = 'ACTIVE'
        ORDER BY submission.submitted_at DESC, submission.id DESC
        LIMIT 1
      `,
      [taskInstanceId, accountId],
    );
    return result.rows[0] ?? null;
  }

  async isRetryAllowed(
    accountId: string,
    taskInstanceId: string,
  ): Promise<boolean> {
    const result = await this.database.queryTide<{
      retryAllowed: boolean;
    }>(
      `
        SELECT COALESCE(
          (execution.content_config->>'allowRetry')::boolean,
          false
        ) AS "retryAllowed"
        FROM public.task_assignments assignment
        JOIN tide.teacher_bindings binding
          ON binding.teacher_id = assignment.teacher_id
         AND binding.account_id = $1
         AND binding.status = 'ACTIVE'
        JOIN tide.task_execution_versions execution
          ON execution.shared_template_row_id = assignment.template_version_id
         AND execution.status = 'ACTIVE'
        WHERE assignment.assignment_id = $2
        LIMIT 1
      `,
      [accountId, taskInstanceId],
    );
    if (!result.rows[0]) throw new OwnedTaskNotFoundError();
    return result.rows[0].retryAllowed === true;
  }

  private async findCommandReplay(
    client: PoolClient,
    input: TaskCommandInput,
  ): Promise<TaskMutationResponse | null> {
    const lockKeys = [
      `task-command:${input.accountId}:${input.commandId}`,
      `task-idempotency:${input.accountId}:${input.idempotencyKey}`,
    ].sort();
    for (const key of lockKeys) {
      await client.query(
        `SELECT pg_advisory_xact_lock(hashtextextended($1, 0))`,
        [key],
      );
    }
    const existing = await client.query<CommandReceiptRow>(
      `
        SELECT
          task_assignment_id AS "taskAssignmentId",
          idempotency_key AS "idempotencyKey",
          command_id AS "commandId",
          request_hash AS "requestHash",
          response_body AS "responseBody"
        FROM tide.task_command_receipts
        WHERE account_id = $1
          AND (idempotency_key = $2 OR command_id = $3)
      `,
      [input.accountId, input.idempotencyKey, input.commandId],
    );
    if (existing.rows.length === 0) {
      return null;
    }
    const receipt = existing.rows[0];
    if (
      existing.rows.length !== 1 ||
      receipt.taskAssignmentId !== input.taskInstanceId ||
      receipt.idempotencyKey !== input.idempotencyKey ||
      receipt.commandId !== input.commandId ||
      receipt.requestHash !== input.requestHash
    ) {
      throw new TaskCommandConflictError('IDEMPOTENCY_CONFLICT');
    }
    return receipt.responseBody;
  }

  private async lockOwnedTask(
    client: PoolClient,
    input: Pick<TaskCommandInput, 'accountId' | 'taskInstanceId'>,
  ): Promise<LockedTaskRow> {
    const result = await client.query<LockedTaskRow>(
      `
        SELECT
          task.assignment_id AS "taskInstanceId",
          task.status,
          task.row_version AS "stateVersion",
          execution.id AS "executionVersionId",
          execution.config AS "contentConfig",
          task.task_kind AS "sourceType",
          task.task_code AS "taskCode",
          COALESCE(
            task.display_title,
            template.payload->>'title',
            task.task_code
          ) AS "taskTitle",
          task.source_mode AS "dataOrigin",
          task.teacher_id AS "teacherId",
          task.assignment_id AS "assignmentId"
        FROM public.task_assignments task
        JOIN tide.teacher_bindings binding ON binding.teacher_id = task.teacher_id
        JOIN tide.task_execution_versions execution
          ON execution.shared_template_row_id = task.template_version_id
        JOIN public.task_templates template
          ON template.row_id = task.template_version_id
        WHERE task.assignment_id = $1
          AND binding.account_id = $2
          AND binding.status = 'ACTIVE'
          AND ${this.stageAvailabilityCondition()}
        FOR UPDATE OF task
      `,
      [input.taskInstanceId, input.accountId],
    );
    if (!result.rows[0]) {
      throw new OwnedTaskNotFoundError();
    }
    return result.rows[0];
  }

  private async updateAssignmentStatus(
    client: PoolClient,
    assignmentId: string,
    expectedStateVersion: number,
    status: TaskStatus,
    reasonCode: string | null,
  ): Promise<number> {
    const result = await client.query<{ rowVersion: number }>(
      `
        UPDATE public.task_assignments
        SET status = $3::varchar,
            status_reason_code = $4,
            status_changed_at = GREATEST(
              clock_timestamp(),
              status_changed_at + interval '1 microsecond'
            ),
            completed_at = CASE WHEN $3::varchar = 'COMPLETED' THEN clock_timestamp() ELSE NULL END,
            updated_by = 'tide_teacher_backend'
        WHERE assignment_id = $1
          AND row_version = $2
        RETURNING row_version AS "rowVersion"
      `,
      [assignmentId, expectedStateVersion, status, reasonCode],
    );
    if (!result.rows[0]) {
      throw new TaskCommandConflictError('STATE_VERSION_CONFLICT', {
        expectedStateVersion,
      });
    }
    return Number(result.rows[0].rowVersion);
  }

  private assertStateVersion(
    task: LockedTaskRow,
    expectedStateVersion: number,
  ): void {
    const actualStateVersion = Number(task.stateVersion);
    if (actualStateVersion !== expectedStateVersion) {
      throw new TaskCommandConflictError('STATE_VERSION_CONFLICT', {
        expectedStateVersion,
        actualStateVersion,
      });
    }
  }

  private assertStatus(task: LockedTaskRow, allowed: TaskStatus[]): void {
    if (!allowed.includes(task.status)) {
      throw new TaskCommandConflictError('INVALID_STATE_TRANSITION', {
        currentStatus: task.status,
        allowedStatuses: allowed,
      });
    }
  }

  private async evaluateStepProgress(
    client: PoolClient,
    task: LockedTaskRow,
    step: StepDefinitionRow,
    progress: Record<string, unknown>,
    requestedPercent: number,
    accountId: string,
  ): Promise<EvaluatedStepProgress> {
    if (step.stepType === 'VIDEO') {
      return this.evaluateVideoProgress(client, task, step, progress);
    }
    if (step.stepType === 'DOCUMENT') {
      const completed = progress.acknowledged === true;
      return {
        status: completed ? 'COMPLETED' : 'IN_PROGRESS',
        percent: completed ? 100 : 0,
        summary: { acknowledged: completed },
      };
    }
    if (step.stepType === 'CHECKLIST') {
      return this.evaluateChecklistProgress(step, progress);
    }
    if (step.stepType === 'DEVICE_CHECK') {
      return this.evaluateDeviceProgress(step, progress);
    }
    if (step.stepType === 'UPLOAD') {
      return this.evaluateUploadProgress(
        client,
        task,
        step,
        progress,
        accountId,
      );
    }
    if (step.stepType === 'CUSTOM' && step.config.kind === 'TEXT_SUBMISSION') {
      const text =
        typeof progress.text === 'string' ? progress.text.trim() : '';
      const minCharacters = Number(step.config.minCharacters ?? 1);
      const maxCharacters = Number(step.config.maxCharacters ?? 20_000);
      if (
        !Number.isInteger(minCharacters) ||
        !Number.isInteger(maxCharacters) ||
        minCharacters < 1 ||
        maxCharacters < minCharacters ||
        text.length > maxCharacters
      ) {
        throw new TaskCommandConflictError('OUTPUT_INVALID', {
          stepKey: step.stepKey,
          reason: 'TEXT_CONFIG_OR_LENGTH_INVALID',
        });
      }
      const completed = text.length >= minCharacters;
      return {
        status: completed ? 'COMPLETED' : 'IN_PROGRESS',
        percent: completed
          ? 100
          : Math.round((text.length / minCharacters) * 90),
        summary: { text },
        result: { characters: text.length, completed },
      };
    }

    const percent = Math.min(100, Math.max(0, requestedPercent));
    return {
      status:
        percent === 100
          ? 'COMPLETED'
          : percent === 0
            ? 'NOT_STARTED'
            : 'IN_PROGRESS',
      percent,
      summary: progress,
    };
  }

  private persistStepProgress(
    client: PoolClient,
    taskInstanceId: string,
    stepKey: string,
    evaluated: EvaluatedStepProgress,
  ): Promise<unknown> {
    return client.query(
      `
        INSERT INTO tide.task_step_progress (
          id, task_assignment_id, step_key, status, percent,
          progress_summary, first_started_at, completed_at
        ) VALUES (
          $1, $2, $3, $4, $5::smallint, $6,
          CASE WHEN $5::smallint > 0 THEN now() ELSE NULL END,
          CASE WHEN $5::smallint = 100 THEN now() ELSE NULL END
        )
        ON CONFLICT (task_assignment_id, step_key) WHERE task_assignment_id IS NOT NULL
        DO UPDATE SET
          status = EXCLUDED.status,
          percent = EXCLUDED.percent,
          progress_summary = EXCLUDED.progress_summary,
          first_started_at = COALESCE(tide.task_step_progress.first_started_at, EXCLUDED.first_started_at),
          completed_at = CASE WHEN EXCLUDED.percent = 100 THEN COALESCE(tide.task_step_progress.completed_at, now()) ELSE NULL END,
          updated_at = now()
      `,
      [
        randomUUID(),
        taskInstanceId,
        stepKey,
        evaluated.status,
        evaluated.percent,
        evaluated.summary,
      ],
    );
  }

  private async evaluateVideoProgress(
    client: PoolClient,
    task: LockedTaskRow,
    step: StepDefinitionRow,
    progress: Record<string, unknown>,
  ): Promise<EvaluatedStepProgress> {
    const configuredDuration = Number(step.config.durationSeconds);
    const position = Number(progress.positionSeconds);
    const playbackRate = Number(progress.playbackRate);
    const mediaVersion = step.config.mediaVersion;
    if (
      !Number.isFinite(configuredDuration) ||
      configuredDuration <= 0 ||
      !Number.isFinite(position) ||
      position < 0 ||
      playbackRate !== 1 ||
      typeof mediaVersion !== 'string'
    ) {
      throw new TaskCommandConflictError('OUTPUT_INVALID', {
        stepKey: step.stepKey,
      });
    }

    const duration = Math.round(configuredDuration);
    const safePosition = Math.min(duration, Math.max(0, Math.floor(position)));
    const current = await client.query<VideoProgressRow>(
      `
        SELECT duration_seconds AS "durationSeconds",
          resume_seconds AS "resumeSeconds",
          max_contiguous_seconds AS "maxContiguousSeconds",
          first_full_watch_at AS "firstFullWatchAt", updated_at AS "updatedAt"
        FROM tide.video_progress
        WHERE task_assignment_id = $1 AND step_key = $2
        FOR UPDATE
      `,
      [task.taskInstanceId, step.stepKey],
    );
    const existing = current.rows[0];
    if (!existing) {
      await client.query(
        `
          INSERT INTO tide.video_progress (
            id, task_assignment_id, step_key, media_version,
            duration_seconds, resume_seconds, max_contiguous_seconds
          ) VALUES ($1, $2, $3, $4, $5, $6, 0)
        `,
        [
          randomUUID(),
          task.taskInstanceId,
          step.stepKey,
          mediaVersion,
          duration,
          safePosition,
        ],
      );
      return {
        status: 'IN_PROGRESS',
        percent: 0,
        summary: { mediaVersion, resumeSeconds: safePosition },
        result: { firstFullWatchComplete: false },
      };
    }

    if (existing.durationSeconds !== duration) {
      throw new TaskCommandConflictError('OUTPUT_INVALID', {
        stepKey: step.stepKey,
        reason: 'MEDIA_VERSION_MISMATCH',
      });
    }
    if (existing.firstFullWatchAt) {
      return {
        status: 'COMPLETED',
        percent: 100,
        summary: { mediaVersion, resumeSeconds: safePosition },
        result: { firstFullWatchComplete: true },
      };
    }

    const serverElapsed = Math.max(
      0,
      (Date.now() - existing.updatedAt.getTime()) / 1000,
    );
    const allowedForward = Math.min(15, serverElapsed + 1.5);
    const observedForward = safePosition - existing.resumeSeconds;
    const contiguousAdvance =
      observedForward >= -1 && observedForward <= allowedForward + 1
        ? Math.max(0, observedForward)
        : 0;
    const maxContiguous = Math.min(
      duration,
      Math.max(
        existing.maxContiguousSeconds,
        existing.maxContiguousSeconds + contiguousAdvance,
      ),
    );
    const completed = maxContiguous >= duration - 1;
    await client.query(
      `
        UPDATE tide.video_progress
        SET resume_seconds = $3, max_contiguous_seconds = $4,
          first_full_watch_at = CASE WHEN $5 THEN COALESCE(first_full_watch_at, now()) ELSE first_full_watch_at END,
          updated_at = now()
        WHERE task_assignment_id = $1 AND step_key = $2
      `,
      [
        task.taskInstanceId,
        step.stepKey,
        safePosition,
        completed ? duration : Math.floor(maxContiguous),
        completed,
      ],
    );
    const percent = completed
      ? 100
      : Math.min(99, Math.floor((maxContiguous / duration) * 100));
    return {
      status: completed ? 'COMPLETED' : 'IN_PROGRESS',
      percent,
      summary: {
        mediaVersion,
        resumeSeconds: safePosition,
        maxContiguousSeconds: completed ? duration : Math.floor(maxContiguous),
      },
      result: { firstFullWatchComplete: completed },
    };
  }

  private evaluateChecklistProgress(
    step: StepDefinitionRow,
    progress: Record<string, unknown>,
  ): EvaluatedStepProgress {
    const items = Array.isArray(step.config.items) ? step.config.items : [];
    const itemKeys = items
      .map((item) =>
        typeof item === 'string'
          ? item
          : item && typeof item === 'object' && 'key' in item
            ? String((item as Record<string, unknown>).key)
            : null,
      )
      .filter((key): key is string => Boolean(key));
    const checked = Array.isArray(progress.checkedItemKeys)
      ? progress.checkedItemKeys.filter(
          (key): key is string => typeof key === 'string',
        )
      : [];
    const checkedSet = new Set(checked);
    const completed =
      itemKeys.length > 0 && itemKeys.every((key) => checkedSet.has(key));
    return {
      status: completed ? 'COMPLETED' : 'IN_PROGRESS',
      percent: completed
        ? 100
        : Math.round(
            (itemKeys.filter((key) => checkedSet.has(key)).length /
              Math.max(1, itemKeys.length)) *
              90,
          ),
      summary: {
        checklistVersion: step.config.version ?? '1',
        checkedItemKeys: checked.filter((key) => itemKeys.includes(key)),
      },
      result: {
        checked: itemKeys.filter((key) => checkedSet.has(key)).length,
        total: itemKeys.length,
      },
    };
  }

  private evaluateDeviceProgress(
    step: StepDefinitionRow,
    progress: Record<string, unknown>,
  ): EvaluatedStepProgress {
    const itemKeys = Array.isArray(step.config.items)
      ? step.config.items.filter(
          (key): key is string => typeof key === 'string',
        )
      : [];
    const results =
      progress.results && typeof progress.results === 'object'
        ? (progress.results as Record<string, unknown>)
        : {};
    const passedCount = itemKeys.filter(
      (key) => results[key] === 'PASSED',
    ).length;
    const completed = itemKeys.length > 0 && passedCount === itemKeys.length;
    return {
      status: completed ? 'COMPLETED' : 'FAILED',
      percent: completed
        ? 100
        : Math.round((passedCount / Math.max(1, itemKeys.length)) * 90),
      summary: { checkVersion: step.config.version ?? '1', results },
      result: { passed: completed, passedCount, total: itemKeys.length },
    };
  }

  private async evaluateUploadProgress(
    client: PoolClient,
    task: LockedTaskRow,
    step: StepDefinitionRow,
    progress: Record<string, unknown>,
    accountId: string,
  ): Promise<EvaluatedStepProgress> {
    const fileId = progress.fileId;
    if (typeof fileId !== 'string') {
      throw new TaskCommandConflictError('OUTPUT_INVALID', {
        stepKey: step.stepKey,
      });
    }
    const readyFile = await client.query(
      `
        SELECT 1
        FROM tide.file_objects file
        JOIN tide.file_upload_intents intent ON intent.file_id = file.id
        WHERE file.id = $1 AND file.status = 'READY'
          AND intent.account_id = $2 AND intent.task_assignment_id = $3
          AND intent.step_key = $4
        LIMIT 1
      `,
      [fileId, accountId, task.taskInstanceId, step.stepKey],
    );
    if (readyFile.rowCount === 0) {
      throw new TaskCommandConflictError('FILE_NOT_READY', {
        stepKey: step.stepKey,
      });
    }
    return {
      status: 'COMPLETED',
      percent: 100,
      summary: { fileId },
      result: { fileReady: true },
    };
  }

  private async loadStepDefinitions(
    client: PoolClient,
    executionVersionId: string,
  ): Promise<StepDefinitionRow[]> {
    const result = await client.query<StepDefinitionRow>(
      `
        SELECT step_key AS "stepKey", step_type AS "stepType", config
        FROM tide.task_step_definitions
        WHERE execution_version_id = $1
        ORDER BY position
      `,
      [executionVersionId],
    );
    return result.rows;
  }

  private async validateOutputs(
    client: PoolClient,
    input: TaskCommandInput & { outputs: StepOutputDto[] },
    definitions: StepDefinitionRow[],
  ): Promise<void> {
    const expectedOutput: Partial<Record<TaskStepType, string>> = {
      CHECKLIST: 'CHECKLIST',
      UPLOAD: 'FILE',
      DEVICE_CHECK: 'DEVICE_CHECK',
      EXTERNAL_TRAINING: 'EXTERNAL_PROOF',
      CUSTOM: 'CUSTOM',
    };
    const definitionsByKey = new Map(
      definitions.map((definition) => [definition.stepKey, definition]),
    );
    const seen = new Set<string>();
    for (const output of input.outputs) {
      const definition = definitionsByKey.get(output.stepKey);
      if (
        !definition ||
        seen.has(output.stepKey) ||
        expectedOutput[definition.stepType] !== output.outputType
      ) {
        throw new TaskCommandConflictError('OUTPUT_INVALID', {
          stepKey: output.stepKey,
        });
      }
      seen.add(output.stepKey);
      if (output.outputType === 'FILE') {
        const fileId = output.value.fileId;
        if (typeof fileId !== 'string') {
          throw new TaskCommandConflictError('OUTPUT_INVALID', {
            stepKey: output.stepKey,
          });
        }
        const readyFile = await client.query(
          `
            SELECT 1
            FROM tide.file_objects file
            JOIN tide.file_upload_intents intent ON intent.file_id = file.id
            WHERE file.id = $1
              AND file.status = 'READY'
              AND intent.account_id = $2
              AND intent.task_assignment_id = $3
              AND intent.step_key = $4
            LIMIT 1
          `,
          [fileId, input.accountId, input.taskInstanceId, output.stepKey],
        );
        if (readyFile.rowCount === 0) {
          throw new TaskCommandConflictError('FILE_NOT_READY', {
            stepKey: output.stepKey,
          });
        }
      }
    }
  }

  private async resolveSubmissionOutputs(
    client: PoolClient,
    taskInstanceId: string,
    definitions: StepDefinitionRow[],
    requested: StepOutputDto[],
  ): Promise<StepOutputDto[]> {
    const rows = await client.query<{
      stepKey: string;
      progressSummary: Record<string, unknown>;
    }>(
      `
        SELECT step_key AS "stepKey", progress_summary AS "progressSummary"
        FROM tide.task_step_progress
        WHERE task_assignment_id = $1
      `,
      [taskInstanceId],
    );
    const summaries = new Map(
      rows.rows.map((row) => [row.stepKey, row.progressSummary]),
    );
    const outputType: Partial<
      Record<TaskStepType, StepOutputDto['outputType']>
    > = {
      CHECKLIST: 'CHECKLIST',
      UPLOAD: 'FILE',
      DEVICE_CHECK: 'DEVICE_CHECK',
      EXTERNAL_TRAINING: 'EXTERNAL_PROOF',
      CUSTOM: 'CUSTOM',
    };
    const merged = new Map(requested.map((output) => [output.stepKey, output]));
    for (const definition of definitions) {
      if (merged.has(definition.stepKey)) continue;
      const type = outputType[definition.stepType];
      const value = summaries.get(definition.stepKey);
      if (!type || !value || Object.keys(value).length === 0) continue;
      merged.set(definition.stepKey, {
        stepKey: definition.stepKey,
        outputType: type,
        value,
      });
    }
    return [...merged.values()];
  }

  private async loadValidationRules(
    client: PoolClient,
    executionVersionId: string,
  ): Promise<TaskValidationRule[]> {
    const result = await client.query<ValidationRuleRow>(
      `
        SELECT
          rule_key AS "ruleKey", rule_type AS "ruleType",
          rule_version AS "ruleVersion", config,
          teacher_failure_copy AS "teacherFailureCopy"
        FROM tide.task_validation_rules
        WHERE execution_version_id = $1
        ORDER BY position
      `,
      [executionVersionId],
    );
    return result.rows;
  }

  private async loadValidationSteps(
    client: PoolClient,
    taskInstanceId: string,
    executionVersionId: string,
  ): Promise<TaskValidationStep[]> {
    const result = await client.query<ValidationStepRow>(
      `
        SELECT
          definition.step_key AS "stepKey",
          COALESCE(progress.status, 'NOT_STARTED') AS status,
          COALESCE(progress.percent, 0) AS percent
        FROM tide.task_step_definitions definition
        LEFT JOIN tide.task_step_progress progress
          ON progress.task_assignment_id = $1
         AND progress.step_key = definition.step_key
        WHERE definition.execution_version_id = $2
        ORDER BY definition.position
      `,
      [taskInstanceId, executionVersionId],
    );
    return result.rows.map((row) => ({
      ...row,
      percent: Number(row.percent),
    }));
  }

  private async linkSubmissionFiles(
    client: PoolClient,
    submissionId: string,
    outputs: StepOutputDto[],
  ): Promise<void> {
    let position = 0;
    for (const output of outputs) {
      if (output.outputType !== 'FILE') {
        continue;
      }
      position += 1;
      await client.query(
        `
          INSERT INTO tide.task_submission_files (
            submission_id, file_id, purpose, position
          ) VALUES ($1, $2, $3, $4)
        `,
        [submissionId, output.value.fileId, output.stepKey, position],
      );
    }
  }

  private async persistProgressEvidence(
    client: PoolClient,
    attemptId: string,
    submissionId: string,
    taskInstanceId: string,
    definitions: StepDefinitionRow[],
  ): Promise<void> {
    const progress = await client.query<StepEvidenceRow>(
      `
        SELECT definition.step_key AS "stepKey",
          definition.step_type AS "stepType", definition.config,
          COALESCE(step.progress_summary, '{}'::jsonb) AS "progressSummary"
        FROM tide.task_step_definitions definition
        LEFT JOIN tide.task_step_progress step
          ON step.task_assignment_id = $1 AND step.step_key = definition.step_key
        WHERE definition.execution_version_id = (
          SELECT execution.id
          FROM public.task_assignments assignment
          JOIN tide.task_execution_versions execution
            ON execution.shared_template_row_id = assignment.template_version_id
          WHERE assignment.assignment_id = $1
        )
        ORDER BY definition.position
      `,
      [taskInstanceId],
    );
    const definitionsByKey = new Map(
      definitions.map((definition) => [definition.stepKey, definition]),
    );
    let filePosition = 0;
    for (const row of progress.rows) {
      const definition = definitionsByKey.get(row.stepKey);
      if (!definition) continue;
      if (row.stepType === 'CHECKLIST') {
        await this.persistChecklistEvidence(client, attemptId, row);
      } else if (row.stepType === 'DEVICE_CHECK') {
        await this.persistDeviceEvidence(client, attemptId, row);
      } else if (
        row.stepType === 'UPLOAD' &&
        typeof row.progressSummary.fileId === 'string'
      ) {
        filePosition += 1;
        await client.query(
          `
            INSERT INTO tide.task_submission_files (
              submission_id, file_id, purpose, position
            ) VALUES ($1, $2, $3, $4)
            ON CONFLICT DO NOTHING
          `,
          [submissionId, row.progressSummary.fileId, row.stepKey, filePosition],
        );
      }
    }
  }

  private async persistChecklistEvidence(
    client: PoolClient,
    attemptId: string,
    row: StepEvidenceRow,
  ): Promise<void> {
    const checklistAttemptId = randomUUID();
    const checked = Array.isArray(row.progressSummary.checkedItemKeys)
      ? new Set(
          row.progressSummary.checkedItemKeys.filter(
            (key): key is string => typeof key === 'string',
          ),
        )
      : new Set<string>();
    await client.query(
      `
        INSERT INTO tide.checklist_attempts (
          id, task_attempt_id, step_key, checklist_version, completed_at
        ) VALUES ($1, $2, $3, $4, now())
      `,
      [
        checklistAttemptId,
        attemptId,
        row.stepKey,
        typeof row.config.version === 'string' ? row.config.version : '1',
      ],
    );
    const items = Array.isArray(row.config.items) ? row.config.items : [];
    for (const item of items) {
      const itemKey =
        typeof item === 'string'
          ? item
          : item && typeof item === 'object' && 'key' in item
            ? String((item as Record<string, unknown>).key)
            : null;
      if (!itemKey) continue;
      await client.query(
        `
          INSERT INTO tide.checklist_item_results (
            id, checklist_attempt_id, item_key, checked, checked_at
          ) VALUES ($1, $2, $3, $4, CASE WHEN $4 THEN now() ELSE NULL END)
        `,
        [randomUUID(), checklistAttemptId, itemKey, checked.has(itemKey)],
      );
    }
  }

  private async persistDeviceEvidence(
    client: PoolClient,
    attemptId: string,
    row: StepEvidenceRow,
  ): Promise<void> {
    const runId = randomUUID();
    const results =
      row.progressSummary.results &&
      typeof row.progressSummary.results === 'object'
        ? (row.progressSummary.results as Record<string, unknown>)
        : {};
    const passed = Object.values(results).every(
      (status) => status === 'PASSED',
    );
    await client.query(
      `
        INSERT INTO tide.device_check_runs (
          id, task_attempt_id, step_key, check_version,
          status, finished_at
        ) VALUES ($1, $2, $3, $4, $5, now())
      `,
      [
        runId,
        attemptId,
        row.stepKey,
        typeof row.config.version === 'string' ? row.config.version : '1',
        passed ? 'PASSED' : 'FAILED',
      ],
    );
    for (const [itemKey, statusValue] of Object.entries(results)) {
      const status =
        statusValue === 'PASSED' ||
        statusValue === 'FAILED' ||
        statusValue === 'ERROR' ||
        statusValue === 'SKIPPED'
          ? statusValue
          : 'ERROR';
      await client.query(
        `
          INSERT INTO tide.device_check_item_results (
            id, device_check_run_id, item_key, status,
            measured_summary, teacher_message
          ) VALUES ($1, $2, $3, $4, '{}'::jsonb, NULL)
        `,
        [randomUUID(), runId, itemKey, status],
      );
    }
  }

  private saveCommandReceipt(
    client: PoolClient,
    input: TaskCommandInput,
    commandType: TaskCommandType,
    response: TaskMutationResponse,
  ): Promise<unknown> {
    return client.query(
      `
        INSERT INTO tide.task_command_receipts (
          id, account_id, task_assignment_id, idempotency_key,
          command_id, command_type, request_hash, response_body
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
      `,
      [
        randomUUID(),
        input.accountId,
        input.taskInstanceId,
        input.idempotencyKey,
        input.commandId,
        commandType,
        input.requestHash,
        response,
      ],
    );
  }

  private createTaskResultNotification(
    client: PoolClient,
    task: LockedTaskRow,
    attemptId: string,
    validationStatus: 'UNDER_REVIEW' | 'PASSED' | 'FAILED',
    resultVersion: string,
  ): Promise<unknown> {
    const content = buildTaskResultNotificationContent(
      task.taskTitle,
      task.taskCode,
      validationStatus,
    );
    return client.query(
      `
        INSERT INTO tide.system_notifications (
          system_notification_id, teacher_id, type_code, title, body,
          action_type, action_target, dedupe_key, payload, issued_at
        ) VALUES (
          $1, $2, $3, $4, $5,
          'TASK_DETAIL', $6, $7,
          jsonb_build_object(
            'taskAssignmentId', $8::text,
            'attemptId', $9::text,
            'resultVersion', $10::text
          ),
          now()
        )
        ON CONFLICT (dedupe_key) DO NOTHING
      `,
      [
        randomUUID(),
        task.teacherId,
        content.typeCode,
        content.title,
        content.body,
        `/task/${task.assignmentId}`,
        `task-result:${task.assignmentId}:${attemptId}:${validationStatus}`,
        task.assignmentId,
        attemptId,
        resultVersion,
      ],
    );
  }

  private mutationResponse(
    taskInstanceId: string,
    status: TaskStatus,
    stateVersion: number,
    validation?: TaskValidationResponse,
    step?: TaskMutationResponse['step'],
  ): TaskMutationResponse {
    return {
      accepted: true,
      taskInstanceId,
      status,
      stateVersion,
      ...(step ? { step } : {}),
      ...(validation ? { validation } : {}),
    };
  }

  private taskSelect(): string {
    return `
      SELECT
        task.assignment_id AS "taskInstanceId",
        task.task_code AS "taskCode",
        task.task_kind AS kind,
        task.status,
        task.row_version AS "stateVersion",
        template.template_version AS "templateVersion",
        execution.execution_contract_version AS "executionContractVersion",
        COALESCE(template.payload->>'content_locale', 'en') AS language,
        execution.config || jsonb_build_object(
          'stageKey', template.payload->>'stage',
          'sequence', COALESCE(
            NULLIF(template.payload->>'sequence', '')::numeric::integer,
            0
          ),
          'points', COALESCE(
            NULLIF(template.payload->>'score_value', '')::numeric::integer,
            0
          )
        ) AS "contentConfig",
        COALESCE(
          NULLIF(task.display_title, ''),
          template.payload->>'title',
          task.task_code
        ) AS title,
        task.why AS why,
        template.payload->>'how_summary' AS "whatToDo",
        template.payload->>'completion_standard' AS "completionStandard",
        template.payload->>'benefit' AS outcome,
        task.priority,
        task.assignment_id AS "assignmentId",
        task.why AS "teacherSafeReason",
        task.evidence_snapshot AS "evidenceSnapshot",
        '[]'::jsonb AS "teacherSafeFacts",
        '[]'::jsonb AS "relatedCourses",
        NULL::varchar AS "reminderNotificationId",
        task.assigned_at AS "availableAt",
        task.due_at AS "dueAt",
        task.completed_at AS "completedAt",
        task.source_mode AS "dataOrigin"
      FROM public.task_assignments task
      JOIN public.task_templates template
        ON template.row_id = task.template_version_id
       AND template.status = 'PUBLISHED'
      JOIN tide.task_execution_versions execution
        ON execution.shared_template_row_id = task.template_version_id
       AND execution.status = 'ACTIVE'
    `;
  }

  private async listTaskRows(teacherId: string): Promise<TaskRow[]> {
    const result = await this.database.queryTide<TaskRow>(
      `${this.taskSelect()}
       WHERE task.teacher_id = $1
         AND ${this.stageAvailabilityCondition()}
       ORDER BY
         CASE task.task_kind WHEN 'FIXED_GROWTH' THEN 0 ELSE 1 END,
         CASE task.priority WHEN 'P0' THEN 0 WHEN 'P1' THEN 1 WHEN 'P2' THEN 2 ELSE 3 END,
         task.task_code,
         task.assignment_id`,
      [teacherId],
    );
    return result.rows;
  }

  private stageAvailabilityCondition(): string {
    return `
      (
        task.task_kind <> 'FIXED_GROWTH'
        OR EXISTS (
          SELECT 1
          FROM public.teachers stage_teacher
          WHERE stage_teacher.teacher_id = task.teacher_id
            AND (
              (
                template.payload->>'stage' IN ('DAY_1_7', 'FOUNDATION')
              )
              OR (
                template.payload->>'stage' IN ('DAY_8_14', 'INTEGRATION')
                AND (
                  stage_teacher.camp_day >= 8
                  OR (
                    SELECT count(*)
                    FROM public.task_assignments previous_stage
                    WHERE previous_stage.teacher_id = task.teacher_id
                      AND previous_stage.task_code IN (
                        'G01', 'G02', 'G03', 'G04'
                      )
                      AND previous_stage.status = 'COMPLETED'
                  ) = 4
                )
              )
              OR (
                template.payload->>'stage' IN ('DAY_15_30', 'ADVANCE')
                AND (
                  stage_teacher.camp_day >= 15
                  OR (
                    SELECT count(*)
                    FROM public.task_assignments previous_stage
                    WHERE previous_stage.teacher_id = task.teacher_id
                      AND previous_stage.task_code IN ('G05', 'G06', 'G07')
                      AND previous_stage.status = 'COMPLETED'
                  ) = 3
                )
              )
            )
        )
      )
    `;
  }

  private toTaskSummary(row: TaskRow): TaskSummary {
    return {
      taskInstanceId: row.taskInstanceId,
      taskCode: row.taskCode,
      kind: row.kind,
      status: row.status,
      stateVersion: Number(row.stateVersion),
      display: this.toDisplayMetadata(row.contentConfig),
      title: row.title,
      priority: row.priority,
      availableAt: row.availableAt?.toISOString() ?? null,
      dueAt: row.dueAt?.toISOString() ?? null,
      completedAt: row.completedAt?.toISOString() ?? null,
      dataOrigin: row.dataOrigin,
    };
  }

  private toTaskContext(task: TaskRow, rows: StepRow[]): TaskContext {
    const steps = rows.map((row) => ({
      stepKey: row.stepKey,
      position: row.position,
      type: row.type,
      title: row.title,
      config: this.publicStepConfig(row.config),
    }));
    const progressSteps = rows.map((row) => ({
      stepKey: row.stepKey,
      status: row.progressStatus,
      percent: Number(row.progressPercent),
      details: this.publicProgressDetails(row.progressSummary),
    }));
    const completed = task.status === 'COMPLETED';
    const percent = completed
      ? 100
      : progressSteps.length === 0
        ? 0
        : Math.round(
            progressSteps.reduce((total, step) => total + step.percent, 0) /
              progressSteps.length,
          );
    const current = completed
      ? null
      : (progressSteps.find((step) => step.status !== 'COMPLETED')?.stepKey ??
        null);
    const assignmentEvidence = this.toAssignmentEvidence(task.evidenceSnapshot);

    return {
      taskInstanceId: task.taskInstanceId,
      taskCode: task.taskCode,
      kind: task.kind,
      templateVersion: task.templateVersion,
      executionContractVersion: task.executionContractVersion,
      status: task.status,
      stateVersion: Number(task.stateVersion),
      display: this.toDisplayMetadata(task.contentConfig),
      content: {
        title: task.title,
        why: task.why,
        whatToDo: task.whatToDo,
        completionStandard: task.completionStandard,
        outcome: task.outcome,
        language: task.language,
      },
      steps,
      progress: { currentStepKey: current, percent, steps: progressSteps },
      execution: {
        contentStatus:
          task.contentConfig.contentStatus === 'READY' ? 'READY' : 'PENDING',
        contentVersion:
          typeof task.contentConfig.contentVersion === 'string'
            ? task.contentConfig.contentVersion
            : null,
        pendingReason:
          typeof task.contentConfig.pendingReason === 'string'
            ? task.contentConfig.pendingReason
            : null,
      },
      capabilities: [...new Set(steps.map((step) => step.type))],
      assignment: {
        assignmentId: task.assignmentId,
        teacherSafeReason: task.teacherSafeReason,
        teacherSafeFacts: this.mergeTeacherSafeFacts(
          assignmentEvidence.teacherSafeFacts,
          this.toTeacherSafeFacts(task.teacherSafeFacts),
        ),
        relatedCourses: this.mergeRelatedCourses(
          assignmentEvidence.relatedCourses,
          this.toRelatedCourses(task.relatedCourses),
        ),
        reminderNotificationId: task.reminderNotificationId,
        priority: task.priority,
        dueAt: task.dueAt?.toISOString() ?? null,
      },
      availableAt: task.availableAt?.toISOString() ?? null,
      dueAt: task.dueAt?.toISOString() ?? null,
      completedAt: task.completedAt?.toISOString() ?? null,
      dataOrigin: task.dataOrigin,
    };
  }

  private toDisplayMetadata(config: Record<string, unknown>) {
    return {
      stageKey: typeof config.stageKey === 'string' ? config.stageKey : null,
      sequence: typeof config.sequence === 'number' ? config.sequence : null,
      points: typeof config.points === 'number' ? config.points : 0,
      estimatedMinutes:
        typeof config.estimatedMinutes === 'number'
          ? config.estimatedMinutes
          : null,
    };
  }

  private toTeacherSafeFacts(value: unknown): TaskTeacherSafeFact[] {
    if (!Array.isArray(value)) return [];
    return value
      .flatMap((item) => {
        if (!this.isRecord(item)) return [];
        const label = this.safeText(item.label);
        const factValue = this.safeText(item.value);
        if (!label || !factValue) return [];
        return [
          {
            label,
            labelZh: this.safeText(item.label_zh ?? item.labelZh),
            value: factValue,
            valueZh: this.safeText(item.value_zh ?? item.valueZh),
          },
        ];
      })
      .slice(0, 20);
  }

  private toAssignmentEvidence(value: unknown): {
    teacherSafeFacts: TaskTeacherSafeFact[];
    relatedCourses: TaskRelatedCourse[];
  } {
    if (!this.isRecord(value)) {
      return { teacherSafeFacts: [], relatedCourses: [] };
    }

    const samples = Array.isArray(value.signal_samples)
      ? value.signal_samples
      : [];
    const lessonIds = Array.isArray(value.lesson_ids)
      ? value.lesson_ids
          .map((lessonId) => this.safeText(lessonId))
          .filter((lessonId): lessonId is string => Boolean(lessonId))
      : [];
    const facts: TaskTeacherSafeFact[] = [];
    const courses = new Map<string, TaskRelatedCourse>();

    for (const lessonId of lessonIds.slice(0, 20)) {
      courses.set(lessonId, {
        lessonId,
        label: 'Related class',
        labelZh: '关联课程',
        summary: 'This class is related to the assigned improvement task.',
        summaryZh: '这节课与当前改善任务相关。',
        occurredAt: null,
      });
    }

    for (const sample of samples.slice(0, 20)) {
      if (!this.isRecord(sample)) continue;
      const lessonId = this.safeText(sample.lesson_id ?? sample.lessonId);
      const why = this.safeText(sample.why);
      if (why && !facts.some((fact) => fact.value === why)) {
        facts.push({
          label: 'What we noticed',
          labelZh: '我们注意到',
          value: why,
          valueZh: why,
        });
      }
      if (lessonId) {
        courses.set(lessonId, {
          lessonId,
          label: 'Related class',
          labelZh: '关联课程',
          summary:
            why ?? 'This class is related to the assigned improvement task.',
          summaryZh: why ?? '这节课与当前改善任务相关。',
          occurredAt: null,
        });
      }
    }

    return {
      teacherSafeFacts: facts.slice(0, 20),
      relatedCourses: [...courses.values()].slice(0, 20),
    };
  }

  private mergeTeacherSafeFacts(
    assignmentFacts: TaskTeacherSafeFact[],
    reminderFacts: TaskTeacherSafeFact[],
  ): TaskTeacherSafeFact[] {
    const merged = new Map<string, TaskTeacherSafeFact>();
    for (const fact of [...assignmentFacts, ...reminderFacts]) {
      merged.set(`${fact.label}\u0000${fact.value}`, fact);
    }
    return [...merged.values()].slice(0, 20);
  }

  private mergeRelatedCourses(
    assignmentCourses: TaskRelatedCourse[],
    reminderCourses: TaskRelatedCourse[],
  ): TaskRelatedCourse[] {
    const merged = new Map<string, TaskRelatedCourse>();
    for (const course of assignmentCourses) {
      merged.set(course.lessonId, course);
    }
    for (const course of reminderCourses) {
      merged.set(course.lessonId, course);
    }
    return [...merged.values()].slice(0, 20);
  }

  private toRelatedCourses(value: unknown): TaskRelatedCourse[] {
    if (!Array.isArray(value)) return [];
    return value
      .flatMap((item) => {
        if (!this.isRecord(item)) return [];
        const lessonId = this.safeText(item.lesson_id ?? item.lessonId);
        const label = this.safeText(item.label);
        const summary = this.safeText(item.summary);
        if (!lessonId || !label || !summary) return [];
        const occurredAt = this.safeText(item.occurred_at ?? item.occurredAt);
        return [
          {
            lessonId,
            label,
            labelZh: this.safeText(item.label_zh ?? item.labelZh),
            summary,
            summaryZh: this.safeText(item.summary_zh ?? item.summaryZh),
            occurredAt:
              occurredAt && !Number.isNaN(Date.parse(occurredAt))
                ? new Date(occurredAt).toISOString()
                : null,
          },
        ];
      })
      .slice(0, 20);
  }

  private isRecord(value: unknown): value is Record<string, unknown> {
    return typeof value === 'object' && value !== null && !Array.isArray(value);
  }

  private safeText(value: unknown): string | null {
    if (typeof value !== 'string') return null;
    const text = value.trim();
    return text.length > 0 ? text.slice(0, 1_000) : null;
  }

  private publicStepConfig(config: Record<string, unknown>) {
    const privateKeys = new Set([
      'answer',
      'answers',
      'answerKey',
      'correctAnswer',
      'correctAnswers',
      'expectedAnswer',
      'prompt',
      'systemPrompt',
      'rubric',
    ]);
    const sanitize = (value: unknown): unknown => {
      if (Array.isArray(value)) return value.map(sanitize);
      if (!value || typeof value !== 'object') return value;
      return Object.fromEntries(
        Object.entries(value as Record<string, unknown>)
          .filter(([key]) => !privateKeys.has(key))
          .map(([key, child]) => [key, sanitize(child)]),
      );
    };
    return sanitize(config) as Record<string, unknown>;
  }

  private publicProgressDetails(
    progress: Record<string, unknown>,
  ): Record<string, unknown> {
    const privateKeys = new Set(['internalReason', 'modelRawResponse']);
    return Object.fromEntries(
      Object.entries(progress).filter(([key]) => !privateKeys.has(key)),
    );
  }

  private assertContentReady(task: LockedTaskRow): void {
    if (task.contentConfig.contentStatus !== 'READY') {
      throw new TaskCommandConflictError('CONTENT_NOT_READY', {
        reason:
          typeof task.contentConfig.pendingReason === 'string'
            ? task.contentConfig.pendingReason
            : 'TASK_CONTENT_PENDING',
      });
    }
  }

  private async assertPreviousStepsComplete(
    client: PoolClient,
    task: LockedTaskRow,
    stepKey: string,
  ): Promise<void> {
    const incomplete = await client.query<{ stepKey: string }>(
      `
        SELECT previous.step_key AS "stepKey"
        FROM tide.task_step_definitions current_step
        JOIN tide.task_step_definitions previous
          ON previous.execution_version_id = current_step.execution_version_id
         AND previous.position < current_step.position
        LEFT JOIN tide.task_step_progress progress
          ON progress.task_assignment_id = $1
         AND progress.step_key = previous.step_key
        WHERE current_step.execution_version_id = $2
          AND current_step.step_key = $3
          AND COALESCE(progress.status, 'NOT_STARTED') <> 'COMPLETED'
        ORDER BY previous.position
        LIMIT 1
      `,
      [task.taskInstanceId, task.executionVersionId, stepKey],
    );
    if (incomplete.rows[0]) {
      throw new TaskCommandConflictError('PREVIOUS_STEP_INCOMPLETE', {
        stepKey,
        previousStepKey: incomplete.rows[0].stepKey,
      });
    }
  }
}
