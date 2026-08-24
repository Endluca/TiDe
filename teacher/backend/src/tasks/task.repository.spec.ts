import { TaskRepository } from './task.repository';
import type { TaskRelatedCourse, TaskTeacherSafeFact } from './task.models';

describe('TaskRepository step ordering', () => {
  const progressTask = (taskCode: string) => ({
    taskCode,
    taskInstanceId: 'assignment-001',
    executionVersionId: 'execution-001',
    contentConfig: {
      independentModules: {
        stepKeys: ['g02-environment-photo', 'g02-courseware-confirmation'],
        allowOutOfOrderProgress: true,
        keepAssignmentInProgressUntilPassed: true,
      },
    },
  });

  const orderingCheck = (repository: TaskRepository) =>
    (
      repository as unknown as {
        assertPreviousStepsComplete(
          client: { query: jest.Mock },
          task: ReturnType<typeof progressTask>,
          stepKey: string,
        ): Promise<void>;
      }
    ).assertPreviousStepsComplete.bind(repository);

  it.each(['g02-environment-photo', 'g02-courseware-confirmation'])(
    'lets the configured G04 part %s save independently',
    async (stepKey) => {
      const query = jest.fn();
      const repository = new TaskRepository({} as never, {} as never);

      await expect(
        orderingCheck(repository)({ query }, progressTask('G04'), stepKey),
      ).resolves.toBeUndefined();
      expect(query).not.toHaveBeenCalled();
    },
  );

  it('keeps previous-step enforcement scoped to tasks outside G04', async () => {
    const query = jest.fn().mockResolvedValue({
      rows: [{ stepKey: 'earlier-step' }],
    });
    const repository = new TaskRepository({} as never, {} as never);

    await expect(
      orderingCheck(repository)(
        { query },
        progressTask('G02'),
        'g02-courseware-confirmation',
      ),
    ).rejects.toMatchObject({
      reason: 'PREVIOUS_STEP_INCOMPLETE',
      details: {
        stepKey: 'g02-courseware-confirmation',
        previousStepKey: 'earlier-step',
      },
    });
    expect(query).toHaveBeenCalledTimes(1);
  });
});

describe('TaskRepository removed G04 device step compatibility', () => {
  it('returns STEP_NOT_FOUND when an old client saves device progress', async () => {
    const query = jest.fn().mockResolvedValue({ rowCount: 0, rows: [] });
    const withTideTransaction = jest.fn(
      async (callback: (client: { query: typeof query }) => Promise<unknown>) =>
        callback({ query }),
    );
    const repository = new TaskRepository(
      { withTideTransaction } as never,
      {} as never,
    );
    Object.assign(repository as object, {
      findCommandReplay: jest.fn().mockResolvedValue(null),
      lockOwnedTask: jest.fn().mockResolvedValue({
        taskInstanceId: 'assignment-001',
        status: 'IN_PROGRESS',
        stateVersion: '7',
        executionVersionId: 'execution-001',
        contentConfig: { contentStatus: 'READY' },
        sourceType: 'FIXED_GROWTH',
        taskCode: 'G04',
        taskTitle: 'Lesson Preparation',
        dataOrigin: 'REAL',
        teacherId: 'teacher-001',
        assignmentId: 'assignment-001',
      }),
    });

    await expect(
      repository.saveProgress({
        accountId: 'account-001',
        taskInstanceId: 'assignment-001',
        idempotencyKey: 'old-client-device-progress',
        commandId: 'command-001',
        requestHash: 'request-hash-001',
        expectedStateVersion: 7,
        stepKey: 'g02-device-check',
        percent: 100,
        progress: {},
      }),
    ).rejects.toMatchObject({
      reason: 'STEP_NOT_FOUND',
      details: { stepKey: 'g02-device-check' },
    });
    expect(query).toHaveBeenCalledWith(
      expect.stringContaining('FROM tide.task_step_definitions'),
      ['execution-001', 'g02-device-check'],
    );
  });

  it('builds task context from current definitions, not historical progress rows', async () => {
    const queryTide = jest
      .fn<
        Promise<{ rows: unknown[] }>,
        [text: string, values?: readonly unknown[]]
      >()
      .mockResolvedValueOnce({
        rows: [
          {
            taskInstanceId: 'assignment-001',
            taskCode: 'G04',
            kind: 'FIXED_GROWTH',
            status: 'IN_PROGRESS',
            stateVersion: '7',
            templateVersion: 1,
            executionContractVersion: 'task-contract-v3',
            language: 'en',
            title: 'Lesson Preparation',
            why: 'Why',
            whatToDo: 'How',
            completionStandard: 'Done',
            outcome: 'Benefit',
            contentConfig: { contentStatus: 'READY' },
            priority: 'P1',
            assignmentId: 'assignment-001',
            teacherSafeReason: 'Why',
            evidenceSnapshot: {},
            teacherSafeFacts: [],
            relatedCourses: [],
            reminderNotificationId: null,
            availableAt: null,
            dueAt: null,
            completedAt: null,
            dataOrigin: 'REAL',
          },
        ],
      })
      .mockResolvedValueOnce({ rows: [] });
    const repository = new TaskRepository({ queryTide } as never, {} as never);

    await expect(
      repository.findTask('account-001', 'assignment-001'),
    ).resolves.toMatchObject({ taskInstanceId: 'assignment-001', steps: [] });

    const stepSql = String(queryTide.mock.calls[1]?.[0]);
    expect(stepSql).toContain('FROM tide.task_step_definitions definition');
    expect(stepSql).toContain('LEFT JOIN tide.task_step_progress progress');
    expect(
      stepSql.indexOf('FROM tide.task_step_definitions definition'),
    ).toBeLessThan(
      stepSql.indexOf('LEFT JOIN tide.task_step_progress progress'),
    );
  });
});

describe('TaskRepository independent G04 submission state', () => {
  const g04Task = {
    taskCode: 'G04',
    taskInstanceId: 'assignment-001',
    stateVersion: '7',
    contentConfig: {
      independentModules: {
        stepKeys: ['g02-environment-photo', 'g02-courseware-confirmation'],
        allowOutOfOrderProgress: true,
        keepAssignmentInProgressUntilPassed: true,
      },
    },
  };
  const decision = (status: 'UNDER_REVIEW' | 'PASSED' | 'FAILED') => ({
    status,
    resultCode:
      status === 'PASSED'
        ? 'ALL_RULES_PASSED'
        : status === 'FAILED'
          ? 'IMAGE_REVIEW_RETRY'
          : 'AI_GATEWAY_UNAVAILABLE',
    teacherMessage: status === 'PASSED' ? null : 'Please retry later.',
    ruleVersion: 'g04:1',
  });

  const stateFinalizer = (repository: TaskRepository) =>
    (
      repository as unknown as {
        applyAssignmentValidationResult(
          client: Record<string, never>,
          task: typeof g04Task,
          input: {
            taskInstanceId: string;
            expectedStateVersion: number;
            attemptId: string;
          },
          validation: ReturnType<typeof decision>,
        ): Promise<{ nextStatus: string; stateVersion: number }>;
      }
    ).applyAssignmentValidationResult.bind(repository);

  it.each(['FAILED', 'UNDER_REVIEW'] as const)(
    'keeps %s module validation in progress without a row-version change or notification',
    async (status) => {
      const repository = new TaskRepository({} as never, {} as never);
      const updateAssignmentStatus = jest.fn();
      const createTaskResultNotification = jest.fn();
      Object.assign(repository as object, {
        updateAssignmentStatus,
        createTaskResultNotification,
      });

      await expect(
        stateFinalizer(repository)(
          {},
          g04Task,
          {
            taskInstanceId: 'assignment-001',
            expectedStateVersion: 7,
            attemptId: 'attempt-001',
          },
          decision(status),
        ),
      ).resolves.toEqual({ nextStatus: 'IN_PROGRESS', stateVersion: 7 });
      expect(updateAssignmentStatus).not.toHaveBeenCalled();
      expect(createTaskResultNotification).not.toHaveBeenCalled();
    },
  );

  it('moves a fully passed G04 through SUBMITTED to COMPLETED and notifies once', async () => {
    const repository = new TaskRepository({} as never, {} as never);
    const updateAssignmentStatus = jest
      .fn()
      .mockResolvedValueOnce(8)
      .mockResolvedValueOnce(9);
    const createTaskResultNotification = jest.fn().mockResolvedValue(undefined);
    Object.assign(repository as object, {
      updateAssignmentStatus,
      createTaskResultNotification,
    });

    await expect(
      stateFinalizer(repository)(
        {},
        g04Task,
        {
          taskInstanceId: 'assignment-001',
          expectedStateVersion: 7,
          attemptId: 'attempt-001',
        },
        decision('PASSED'),
      ),
    ).resolves.toEqual({ nextStatus: 'COMPLETED', stateVersion: 9 });
    expect(updateAssignmentStatus).toHaveBeenNthCalledWith(
      1,
      {},
      'assignment-001',
      7,
      'SUBMITTED',
      null,
    );
    expect(updateAssignmentStatus).toHaveBeenNthCalledWith(
      2,
      {},
      'assignment-001',
      8,
      'COMPLETED',
      null,
    );
    expect(createTaskResultNotification).toHaveBeenCalledWith(
      {},
      g04Task,
      'attempt-001',
      'PASSED',
      'g04:1',
    );
  });
});

describe('TaskRepository browser device evidence', () => {
  const step = {
    stepKey: 'g02-device-check',
    stepType: 'DEVICE_CHECK' as const,
    config: {
      version: 'g02-device-2026-08-05-browser-preflight-v1',
      items: ['camera', 'microphone', 'network'],
    },
  };

  const deviceEvaluator = (repository: TaskRepository) =>
    (
      repository as unknown as {
        evaluateDeviceProgress(
          definition: typeof step,
          progress: Record<string, unknown>,
        ): {
          status: string;
          percent: number;
          summary: Record<string, unknown>;
        };
      }
    ).evaluateDeviceProgress.bind(repository);

  it('keeps only whitelisted browser-local evidence and network duration', () => {
    const repository = new TaskRepository({} as never, {} as never);
    const evaluated = deviceEvaluator(repository)(step, {
      results: {
        camera: 'PASSED',
        microphone: 'PASSED',
        network: 'PASSED',
      },
      source: 'BROWSER_LOCAL',
      checkedAt: '2026-08-05T10:00:00.000Z',
      measurements: {
        network: { durationMs: 42.6, ipAddress: 'must-not-be-saved' },
      },
      deviceId: 'must-not-be-saved',
    });

    expect(evaluated).toMatchObject({
      status: 'COMPLETED',
      percent: 100,
      summary: {
        source: 'BROWSER_LOCAL',
        checkedAt: '2026-08-05T10:00:00.000Z',
        measurements: { network: { durationMs: 43 } },
      },
    });
    expect(JSON.stringify(evaluated.summary)).not.toContain('ipAddress');
    expect(JSON.stringify(evaluated.summary)).not.toContain('deviceId');
    expect(JSON.stringify(evaluated.summary)).not.toContain(
      'must-not-be-saved',
    );
  });

  it('rejects device results without the required audit metadata', () => {
    const repository = new TaskRepository({} as never, {} as never);
    let caught: unknown;
    try {
      deviceEvaluator(repository)(step, {
        results: {
          camera: 'PASSED',
          microphone: 'PASSED',
          network: 'PASSED',
        },
      });
    } catch (error) {
      caught = error;
    }
    expect(caught).toMatchObject({
      reason: 'OUTPUT_INVALID',
      details: {
        stepKey: 'g02-device-check',
        reason: 'DEVICE_CHECK_EVIDENCE_INVALID',
      },
    });
  });

  it('writes the safe measurement summary into device evidence rows', async () => {
    const repository = new TaskRepository({} as never, {} as never);
    const query = jest
      .fn<Promise<{ rows: never[] }>, [string, unknown[]?]>()
      .mockResolvedValue({ rows: [] });
    const persist = (
      repository as unknown as {
        persistDeviceEvidence(
          client: { query: typeof query },
          attemptId: string,
          row: {
            stepKey: string;
            stepType: 'DEVICE_CHECK';
            config: Record<string, unknown>;
            progressSummary: Record<string, unknown>;
          },
        ): Promise<void>;
      }
    ).persistDeviceEvidence.bind(repository);

    await persist({ query }, 'attempt-001', {
      ...step,
      progressSummary: {
        checkVersion: 'g02-device-2026-08-05-browser-preflight-v1',
        results: {
          camera: 'PASSED',
          microphone: 'PASSED',
          network: 'PASSED',
        },
        source: 'BROWSER_LOCAL',
        checkedAt: '2026-08-05T10:00:00.000Z',
        measurements: { network: { durationMs: 43 } },
      },
    });

    const itemCalls = query.mock.calls.filter(([sql]) =>
      String(sql).includes('INSERT INTO tide.device_check_item_results'),
    );
    expect(itemCalls).toHaveLength(3);
    const networkCall = itemCalls.find(
      ([, params]) => params?.[2] === 'network',
    );
    expect(networkCall?.[1]?.[4]).toEqual({
      source: 'BROWSER_LOCAL',
      checkedAt: '2026-08-05T10:00:00.000Z',
      durationMs: 43,
    });
  });

  it('projects stale G04 device progress as not started without details', () => {
    const repository = new TaskRepository({} as never, {} as never);
    const context = (
      repository as unknown as {
        toTaskContext(
          task: Record<string, unknown>,
          rows: unknown[],
        ): {
          status: string;
          progress: {
            percent: number;
            steps: Array<{
              stepKey: string;
              status: string;
              percent: number;
              details: Record<string, unknown>;
            }>;
          };
        };
      }
    ).toTaskContext(
      {
        taskInstanceId: 'assignment-001',
        taskCode: 'G04',
        kind: 'FIXED_GROWTH',
        status: 'IN_PROGRESS',
        stateVersion: '7',
        templateVersion: 1,
        executionContractVersion: 'task-contract-v3',
        language: 'en',
        title: 'G04',
        why: 'Why',
        whatToDo: 'How',
        completionStandard: 'Done',
        outcome: 'Benefit',
        contentConfig: { contentStatus: 'READY' },
        priority: 'P1',
        assignmentId: 'assignment-001',
        teacherSafeReason: 'Why',
        evidenceSnapshot: {},
        teacherSafeFacts: [],
        relatedCourses: [],
        reminderNotificationId: null,
        availableAt: null,
        dueAt: null,
        completedAt: null,
        dataOrigin: 'REAL',
      },
      [
        {
          stepKey: step.stepKey,
          position: 1,
          type: step.stepType,
          title: 'Device check',
          config: step.config,
          progressStatus: 'COMPLETED',
          progressPercent: 100,
          progressSummary: {
            checkVersion: 'legacy-device-v1',
            results: {
              camera: 'PASSED',
              microphone: 'PASSED',
              network: 'PASSED',
            },
          },
        },
      ],
    );

    expect(context.status).toBe('IN_PROGRESS');
    expect(context.progress).toMatchObject({
      percent: 0,
      steps: [
        {
          stepKey: 'g02-device-check',
          status: 'NOT_STARTED',
          percent: 0,
          details: {},
        },
      ],
    });
  });

  it('fails final validation closed for stale completed G04 device progress', async () => {
    const repository = new TaskRepository({} as never, {} as never);
    const query = jest.fn().mockResolvedValue({
      rows: [
        {
          stepKey: step.stepKey,
          type: step.stepType,
          config: step.config,
          status: 'COMPLETED',
          percent: 100,
          progressSummary: {
            checkVersion: 'legacy-device-v1',
            results: {
              camera: 'PASSED',
              microphone: 'PASSED',
              network: 'PASSED',
            },
          },
        },
      ],
    });
    const load = (
      repository as unknown as {
        loadValidationSteps(
          client: { query: typeof query },
          taskInstanceId: string,
          executionVersionId: string,
          taskCode: string,
        ): Promise<Array<{ stepKey: string; status: string; percent: number }>>;
      }
    ).loadValidationSteps.bind(repository);

    await expect(
      load({ query }, 'assignment-001', 'execution-001', 'G04'),
    ).resolves.toEqual([
      {
        stepKey: 'g02-device-check',
        status: 'NOT_STARTED',
        percent: 0,
      },
    ]);
  });

  it('does not turn stale device progress into a new audit run', async () => {
    const repository = new TaskRepository({} as never, {} as never);
    const query = jest.fn().mockResolvedValue({ rows: [] });
    const persist = (
      repository as unknown as {
        persistDeviceEvidence(
          client: { query: typeof query },
          attemptId: string,
          row: {
            stepKey: string;
            stepType: 'DEVICE_CHECK';
            config: Record<string, unknown>;
            progressSummary: Record<string, unknown>;
          },
        ): Promise<void>;
      }
    ).persistDeviceEvidence.bind(repository);

    await persist({ query }, 'attempt-001', {
      ...step,
      progressSummary: {
        checkVersion: 'legacy-device-v1',
        results: {
          camera: 'PASSED',
          microphone: 'PASSED',
          network: 'PASSED',
        },
      },
    });

    expect(query).not.toHaveBeenCalled();
  });
});

describe('TaskRepository versioned document reading progress', () => {
  const step = {
    stepKey: 'g02-policy-document',
    stepType: 'DOCUMENT' as const,
    config: {
      contentVersion: '2026-07-24-overseas-nt-policies-v1',
      contentHash:
        '6875233667c6f3d90602a07c84849dbb88f41685929779a7b0dc79ccf859979c',
    },
  };
  const documentTask = {
    taskInstanceId: 'assignment-g02',
  };
  const documentEvaluator = (repository: TaskRepository) =>
    (
      repository as unknown as {
        evaluateDocumentProgress(
          client: { query: jest.Mock },
          task: { taskInstanceId: string },
          definition: typeof step,
          progress: Record<string, unknown>,
          requestedPercent: number,
        ): Promise<{
          status: string;
          percent: number;
          summary: Record<string, unknown>;
        }>;
      }
    ).evaluateDocumentProgress.bind(repository);
  const progress = (readPercent: number, reachedEnd = false) => ({
    contentVersion: step.config.contentVersion,
    contentHash: step.config.contentHash,
    readPercent,
    reachedEnd,
  });

  it('does not expose the internal DingTalk source node id', () => {
    const repository = new TaskRepository({} as never, {} as never);
    const publicStepConfig = (
      repository as unknown as {
        publicStepConfig(
          config: Record<string, unknown>,
        ): Record<string, unknown>;
      }
    ).publicStepConfig.bind(repository);

    expect(
      publicStepConfig({
        sourceNodeId: 'internal-dingtalk-node',
        sourceTitle: 'Overseas NT Policies',
        sourceUpdatedAt: '2026-07-24T01:47:08Z',
        contentVersion: step.config.contentVersion,
        contentHash: step.config.contentHash,
      }),
    ).toEqual({
      sourceTitle: 'Overseas NT Policies',
      sourceUpdatedAt: '2026-07-24T01:47:08Z',
      contentVersion: step.config.contentVersion,
      contentHash: step.config.contentHash,
    });
  });

  it('keeps document progress incomplete until the current version reaches its end', async () => {
    const repository = new TaskRepository({} as never, {} as never);
    const query = jest.fn().mockResolvedValue({ rows: [] });

    await expect(
      documentEvaluator(repository)(
        { query },
        documentTask,
        step,
        progress(99),
        99,
      ),
    ).resolves.toMatchObject({
      status: 'IN_PROGRESS',
      percent: 99,
      summary: { readPercent: 99, reachedEnd: false },
    });
    await expect(
      documentEvaluator(repository)(
        { query },
        documentTask,
        step,
        progress(100, true),
        100,
      ),
    ).resolves.toMatchObject({
      status: 'COMPLETED',
      percent: 100,
      summary: { readPercent: 100, reachedEnd: true },
    });
  });

  it.each([
    {
      taskCode: 'G02',
      taskInstanceId: 'assignment-g02',
      stepKey: 'g02-policy-document',
    },
    {
      taskCode: 'P-REL-MEMO',
      taskInstanceId: 'assignment-p-rel-memo',
      stepKey: 'p-rel-memo-document',
    },
  ])(
    'completes $taskCode in the same transaction that persists the end-of-document state',
    async ({ taskCode, taskInstanceId, stepKey }) => {
      const documentStep = { ...step, stepKey };
      const client = {
        query: jest
          .fn()
          .mockResolvedValue({ rowCount: 1, rows: [documentStep] }),
      };
      const withTideTransaction = jest.fn(
        async (operation: (transaction: typeof client) => Promise<unknown>) =>
          operation(client),
      );
      const repository = new TaskRepository(
        { withTideTransaction } as never,
        {} as never,
      );
      const persistStepProgress = jest.fn().mockResolvedValue(undefined);
      const submitLockedTask = jest.fn().mockResolvedValue({
        accepted: true,
        taskInstanceId: 'assignment-g02',
        status: 'COMPLETED',
        stateVersion: 8,
        validation: {
          status: 'PASSED',
          resultCode: 'ALL_STEPS_COMPLETE',
          teacherMessage: null,
        },
      });
      const saveCommandReceipt = jest.fn().mockResolvedValue(undefined);
      Object.assign(repository as object, {
        findCommandReplay: jest.fn().mockResolvedValue(null),
        lockOwnedTask: jest.fn().mockResolvedValue({
          taskCode,
          taskInstanceId,
          executionVersionId: `execution-${taskCode.toLowerCase()}`,
          stateVersion: '7',
        }),
        assertStateVersion: jest.fn(),
        assertStatus: jest.fn(),
        assertPreviousStepsComplete: jest.fn().mockResolvedValue(undefined),
        evaluateStepProgress: jest.fn().mockResolvedValue({
          status: 'COMPLETED',
          percent: 100,
          summary: progress(100, true),
          result: { reachedEnd: true },
          reachedEnd: true,
        }),
        persistStepProgress,
        submitLockedTask,
        saveCommandReceipt,
      });

      const response = await repository.saveProgress({
        accountId: 'account-document',
        taskInstanceId,
        idempotencyKey: `progress-${taskCode}-100`,
        commandId: `command-${taskCode}-100`,
        requestHash: 'request-hash',
        expectedStateVersion: 7,
        stepKey,
        percent: 100,
        progress: progress(100, true),
      });

      expect(withTideTransaction).toHaveBeenCalledTimes(1);
      expect(persistStepProgress).toHaveBeenCalledTimes(1);
      expect(submitLockedTask).toHaveBeenCalledWith(
        client,
        expect.objectContaining({ taskCode }),
        expect.objectContaining({
          taskInstanceId,
          outputs: [
            {
              stepKey,
              outputType: 'DOCUMENT',
              value: progress(100, true),
            },
          ],
        }),
      );
      expect(response).toMatchObject({
        status: 'COMPLETED',
        stateVersion: 8,
        step: {
          stepKey,
          status: 'COMPLETED',
          percent: 100,
          details: { reachedEnd: true },
        },
        validation: { status: 'PASSED' },
      });
      expect(saveCommandReceipt).toHaveBeenCalledWith(
        client,
        expect.any(Object),
        'PROGRESS',
        response,
      );
    },
  );

  it('keeps the highest server-side percentage when a later report is lower', async () => {
    const repository = new TaskRepository({} as never, {} as never);
    const query = jest.fn().mockResolvedValue({
      rows: [
        {
          status: 'IN_PROGRESS',
          percent: 80,
          progressSummary: progress(80),
          reachedEnd: false,
        },
      ],
    });

    await expect(
      documentEvaluator(repository)(
        { query },
        documentTask,
        step,
        progress(45),
        45,
      ),
    ).resolves.toMatchObject({
      status: 'IN_PROGRESS',
      percent: 80,
      summary: { readPercent: 80, reachedEnd: false },
    });
  });

  it.each([
    [progress(100, false), 100],
    [progress(99, true), 99],
    [{ ...progress(50), contentVersion: 'stale-version' }, 50],
    [{ ...progress(50), contentHash: '0'.repeat(64) }, 50],
    [{ ...progress(50), readPercent: 50.5 }, 50],
    [progress(50), 49],
    [{ acknowledged: true }, 0],
  ])(
    'rejects inconsistent or stale document evidence',
    async (input, percent) => {
      const repository = new TaskRepository({} as never, {} as never);
      const query = jest.fn().mockResolvedValue({ rows: [] });

      await expect(
        documentEvaluator(repository)(
          { query },
          documentTask,
          step,
          input,
          percent,
        ),
      ).rejects.toMatchObject({
        reason: 'OUTPUT_INVALID',
        details: {
          stepKey: 'g02-policy-document',
          reason: 'DOCUMENT_PROGRESS_INVALID',
        },
      });
    },
  );

  it('persists the typed reached_end field with the document progress', async () => {
    const repository = new TaskRepository({} as never, {} as never);
    const query = jest.fn().mockResolvedValue({ rows: [] });
    const persist = (
      repository as unknown as {
        persistStepProgress(
          client: { query: typeof query },
          taskInstanceId: string,
          stepKey: string,
          evaluated: {
            status: 'COMPLETED';
            percent: 100;
            summary: Record<string, unknown>;
            reachedEnd: true;
          },
        ): Promise<unknown>;
      }
    ).persistStepProgress.bind(repository);

    await persist({ query }, 'assignment-g02', step.stepKey, {
      status: 'COMPLETED',
      percent: 100,
      summary: progress(100, true),
      reachedEnd: true,
    });

    expect(query).toHaveBeenCalledWith(
      expect.stringContaining('progress_summary, reached_end'),
      expect.arrayContaining([
        'assignment-g02',
        'g02-policy-document',
        'COMPLETED',
        100,
        true,
      ]),
    );
  });

  it('fails a completed JSON summary closed when the typed field is false', async () => {
    const repository = new TaskRepository({} as never, {} as never);
    const query = jest.fn().mockResolvedValue({
      rows: [
        {
          stepKey: step.stepKey,
          type: step.stepType,
          config: step.config,
          status: 'COMPLETED',
          percent: 100,
          progressSummary: progress(100, true),
          reachedEnd: false,
        },
      ],
    });
    const load = (
      repository as unknown as {
        loadValidationSteps(
          client: { query: typeof query },
          taskInstanceId: string,
          executionVersionId: string,
          taskCode: string,
        ): Promise<Array<{ stepKey: string; status: string; percent: number }>>;
      }
    ).loadValidationSteps.bind(repository);

    await expect(
      load({ query }, 'assignment-g02', 'execution-g02', 'G02'),
    ).resolves.toEqual([
      {
        stepKey: 'g02-policy-document',
        status: 'NOT_STARTED',
        percent: 0,
      },
    ]);
  });

  it('fails stale completed document progress closed during final validation', async () => {
    const repository = new TaskRepository({} as never, {} as never);
    const query = jest.fn().mockResolvedValue({
      rows: [
        {
          stepKey: step.stepKey,
          type: step.stepType,
          config: step.config,
          status: 'COMPLETED',
          percent: 100,
          progressSummary: {
            ...progress(100, true),
            contentVersion: 'stale-version',
          },
          reachedEnd: true,
        },
      ],
    });
    const load = (
      repository as unknown as {
        loadValidationSteps(
          client: { query: typeof query },
          taskInstanceId: string,
          executionVersionId: string,
          taskCode: string,
        ): Promise<Array<{ stepKey: string; status: string; percent: number }>>;
      }
    ).loadValidationSteps.bind(repository);

    await expect(
      load({ query }, 'assignment-g02', 'execution-g02', 'G02'),
    ).resolves.toEqual([
      {
        stepKey: 'g02-policy-document',
        status: 'NOT_STARTED',
        percent: 0,
      },
    ]);
  });
});

describe('TaskRepository versioned G04 guidance evidence', () => {
  const step = {
    stepKey: 'g02-courseware-confirmation',
    stepType: 'CHECKLIST' as const,
    config: {
      version: 'g02-courseware-2026-08-05-guidance-v1',
      role: 'COURSEWARE_CONFIRMATION',
      items: [{ key: 'courseware-prepared' }],
    },
  };
  const staleSummary = {
    checklistVersion: 'g02-courseware-2026-07-28',
    checkedItemKeys: ['courseware-prepared'],
  };

  it('fails final validation closed for the old courseware-only confirmation', async () => {
    const repository = new TaskRepository({} as never, {} as never);
    const query = jest.fn().mockResolvedValue({
      rows: [
        {
          stepKey: step.stepKey,
          type: step.stepType,
          config: step.config,
          status: 'COMPLETED',
          percent: 100,
          progressSummary: staleSummary,
        },
      ],
    });
    const load = (
      repository as unknown as {
        loadValidationSteps(
          client: { query: typeof query },
          taskInstanceId: string,
          executionVersionId: string,
          taskCode: string,
        ): Promise<Array<{ stepKey: string; status: string; percent: number }>>;
      }
    ).loadValidationSteps.bind(repository);

    await expect(
      load({ query }, 'assignment-001', 'execution-001', 'G04'),
    ).resolves.toEqual([
      {
        stepKey: 'g02-courseware-confirmation',
        status: 'NOT_STARTED',
        percent: 0,
      },
    ]);
  });

  it('does not merge stale guidance progress into submission outputs', async () => {
    const repository = new TaskRepository({} as never, {} as never);
    const query = jest.fn().mockResolvedValue({
      rows: [{ stepKey: step.stepKey, progressSummary: staleSummary }],
    });
    const resolve = (
      repository as unknown as {
        resolveSubmissionOutputs(
          client: { query: typeof query },
          taskInstanceId: string,
          definitions: Array<typeof step>,
          requested: never[],
          taskCode: string,
        ): Promise<unknown[]>;
      }
    ).resolveSubmissionOutputs.bind(repository);

    await expect(
      resolve({ query }, 'assignment-001', [step], [], 'G04'),
    ).resolves.toEqual([]);
  });

  it('does not relabel stale guidance progress as current audit evidence', async () => {
    const repository = new TaskRepository({} as never, {} as never);
    const query = jest.fn().mockResolvedValue({ rows: [] });
    const persist = (
      repository as unknown as {
        persistChecklistEvidence(
          client: { query: typeof query },
          attemptId: string,
          row: {
            stepKey: string;
            stepType: 'CHECKLIST';
            config: Record<string, unknown>;
            progressSummary: Record<string, unknown>;
          },
        ): Promise<void>;
      }
    ).persistChecklistEvidence.bind(repository);

    await persist({ query }, 'attempt-001', {
      ...step,
      progressSummary: staleSummary,
    });

    expect(query).not.toHaveBeenCalled();
  });
});

describe('TaskRepository assignment evidence projection', () => {
  it('reads all four teacher-facing summary fields from Shiwen shared tables', () => {
    const repository = new TaskRepository(
      {} as never,
      {} as never,
    ) as unknown as {
      taskSelect(): string;
    };

    const select = repository.taskSelect();

    expect(select).toContain('task.why AS why');
    expect(select).toContain(
      'template.payload->>\'how_summary\' AS "whatToDo"',
    );
    expect(select).toContain(
      'template.payload->>\'completion_standard\' AS "completionStandard"',
    );
    expect(select).toContain("template.payload->>'benefit' AS outcome");
    expect(select).toContain("template.status = 'PUBLISHED'");
    expect(select).toContain("execution.status = 'ACTIVE'");
  });

  it('opens the first stage without a camp-day threshold and gates later stages', () => {
    const repository = new TaskRepository(
      {} as never,
      {} as never,
    ) as unknown as {
      stageAvailabilityCondition(): string;
    };

    const condition = repository.stageAvailabilityCondition();

    expect(condition).toContain(
      "template.payload->>'stage' IN ('DAY_1_7', 'FOUNDATION')",
    );
    expect(condition).not.toMatch(/stage_teacher\.camp_day >= 1\b/);
    expect(condition).toContain('stage_teacher.camp_day >= 8');
    expect(condition).toContain(
      "previous_stage.task_code IN (\n                        'G01', 'G02', 'G03', 'G04'",
    );
    expect(condition).toContain(') = 4');
    expect(condition).toContain('stage_teacher.camp_day >= 15');
    expect(condition).toContain(
      "previous_stage.task_code IN ('G05', 'G06', 'G07')",
    );
    expect(
      condition.match(/previous_stage.status = 'COMPLETED'/g),
    ).toHaveLength(2);
  });

  it('projects lesson ids and safe reasons without exposing internal evidence', () => {
    const repository = new TaskRepository(
      {} as never,
      {} as never,
    ) as unknown as {
      toAssignmentEvidence(value: unknown): {
        teacherSafeFacts: TaskTeacherSafeFact[];
        relatedCourses: TaskRelatedCourse[];
      };
    };

    const projection = repository.toAssignmentEvidence({
      lesson_ids: ['lesson-001', 'lesson-002'],
      signal_samples: [
        {
          lesson_id: 'lesson-001',
          why: 'A recent class showed an attendance issue.',
          evidence: {
            source_row_number: 123,
            internal_risk_label: 'must-not-leak',
          },
        },
      ],
    });

    expect(projection.teacherSafeFacts).toEqual([
      {
        label: 'What we noticed',
        labelZh: '我们注意到',
        value: 'A recent class showed an attendance issue.',
        valueZh: 'A recent class showed an attendance issue.',
      },
    ]);
    expect(projection.relatedCourses).toHaveLength(2);
    expect(JSON.stringify(projection)).not.toContain('source_row_number');
    expect(JSON.stringify(projection)).not.toContain('must-not-leak');
  });
});

describe('TaskRepository batch task contexts', () => {
  it('loads every task and its steps with a constant number of queries', async () => {
    const task = (taskInstanceId: string, taskCode: string) => ({
      taskInstanceId,
      taskCode,
      kind: 'FIXED_GROWTH',
      status: 'ASSIGNED',
      stateVersion: '1',
      templateVersion: 1,
      executionContractVersion: 'v1',
      language: 'en',
      title: taskCode,
      why: 'Why',
      whatToDo: 'How',
      completionStandard: 'Done',
      outcome: 'Benefit',
      contentConfig: {
        contentStatus: 'READY',
        stageKey: 'FOUNDATION',
        sequence: 1,
        points: 1,
      },
      priority: 'P1',
      assignmentId: taskInstanceId,
      teacherSafeReason: 'Why',
      evidenceSnapshot: {},
      teacherSafeFacts: [],
      relatedCourses: [],
      reminderNotificationId: null,
      availableAt: null,
      dueAt: null,
      completedAt: null,
      dataOrigin: 'REAL',
    });
    const queryTide = jest
      .fn()
      .mockResolvedValueOnce({
        rows: [task('assignment-001', 'G01'), task('assignment-002', 'G02')],
      })
      .mockResolvedValueOnce({
        rows: [
          {
            taskInstanceId: 'assignment-001',
            stepKey: 'profile',
            position: 1,
            type: 'CHECKLIST',
            title: 'Profile',
            config: { items: [] },
            progressStatus: 'NOT_STARTED',
            progressPercent: 0,
            progressSummary: {},
          },
          {
            taskInstanceId: 'assignment-002',
            stepKey: 'device',
            position: 1,
            type: 'DEVICE_CHECK',
            title: 'Device',
            config: {},
            progressStatus: 'NOT_STARTED',
            progressPercent: 0,
            progressSummary: {},
          },
        ],
      });
    const repository = new TaskRepository({ queryTide } as never, {} as never);

    const result = await repository.listTasksWithContexts('TEACHER-001');

    expect(queryTide).toHaveBeenCalledTimes(2);
    expect(result.items).toHaveLength(2);
    expect(result.contexts.map((context) => context.taskInstanceId)).toEqual([
      'assignment-001',
      'assignment-002',
    ]);
    expect(result.contexts[0].steps).toHaveLength(1);
    expect(result.contexts[1].steps).toHaveLength(1);
  });
});

describe('TaskRepository latest validation', () => {
  it('returns the latest saved image-review items without changing the schema', async () => {
    const imageReview = {
      criteriaVersion:
        'lesson-preparation-camera-view-2026-08-v7-background-veto',
      decision: 'PASS',
      teacherReason: '四项均已通过。',
      confidenceSummary: { criteria: { background: 0.97 } },
      items: [
        {
          criterionKey: 'background',
          result: 'PASS',
          teacherMessage: null,
        },
      ],
    };
    const queryTide = jest.fn().mockResolvedValue({
      rows: [
        {
          status: 'FAILED',
          resultCode: 'STEPS_INCOMPLETE',
          teacherMessage: '请完成备课确认。',
          imageReview,
        },
      ],
    });
    const repository = new TaskRepository({ queryTide } as never, {} as never);

    await expect(
      repository.getLatestValidation('account-001', 'assignment-001'),
    ).resolves.toMatchObject({ imageReview });
    for (const fragment of [
      'LEFT JOIN LATERAL',
      'tide.image_review_items',
      'review.submission_id = submission.id',
    ]) {
      expect(queryTide).toHaveBeenCalledWith(
        expect.stringContaining(fragment),
        ['assignment-001', 'account-001'],
      );
    }
  });
});

describe('TaskRepository personalized environment photo activation', () => {
  const pendingConfig = {
    contentStatus: 'PENDING',
    pendingReason: 'JIAHE_PERSONALIZED_CONTENT_PENDING',
    contentVersion: 'personalized-photo-v1',
  };
  const evidence = (label: unknown) => ({
    signal_samples: [
      {
        evidence: {
          negative_feedback_label: label,
        },
      },
    ],
  });
  const resolve = (repository: TaskRepository) =>
    (
      repository as unknown as {
        resolveAssignmentContent<T extends Record<string, unknown>>(task: T): T;
      }
    ).resolveAssignmentContent.bind(repository);

  it.each(['灯光过暗/亮', '环境乱/灯光差'])(
    'resolves the exact %s signal to the teaching-environment photo variant',
    (label) => {
      const repository = new TaskRepository({} as never, {} as never);

      expect(
        resolve(repository)({
          taskCode: 'P-FB-NEGATIVE',
          contentConfig: pendingConfig,
          evidenceSnapshot: evidence(label),
        }),
      ).toMatchObject({
        contentConfig: {
          contentStatus: 'READY',
          pendingReason: null,
          contentVersion: 'personalized-photo-v1',
          contentVariant: 'TEACHING_ENVIRONMENT_PHOTO',
        },
      });
    },
  );

  it('prefers the exact stable teacher execution variant', () => {
    const repository = new TaskRepository({} as never, {} as never);

    expect(
      resolve(repository)({
        taskCode: 'P-FB-NEGATIVE',
        contentConfig: pendingConfig,
        evidenceSnapshot: {
          teacher_execution_variant: 'TEACHING_ENVIRONMENT_PHOTO',
        },
      }),
    ).toMatchObject({
      contentConfig: {
        contentStatus: 'READY',
        pendingReason: null,
        contentVariant: 'TEACHING_ENVIRONMENT_PHOTO',
      },
    });
  });

  it.each(['TEACHING_ENVIRONMENT_PHOTO ', 'teaching_environment_photo', null])(
    'fails closed for non-exact stable variant %p',
    (variant) => {
      const repository = new TaskRepository({} as never, {} as never);
      const task = {
        taskCode: 'P-FB-NEGATIVE',
        contentConfig: pendingConfig,
        evidenceSnapshot: {
          teacher_execution_variant: variant,
          signal_samples: [
            { evidence: { negative_feedback_label: '灯光过暗/亮' } },
          ],
        },
      };

      expect(resolve(repository)(task)).toBe(task);
      expect(task.contentConfig.contentStatus).toBe('PENDING');
    },
  );

  it.each([
    ['P-FB-COMPLAINT', 'PENDING'],
    ['P-FB-NEGATIVE', 'READY'],
  ])(
    'does not activate the stable variant for task %s with base status %s',
    (taskCode, contentStatus) => {
      const repository = new TaskRepository({} as never, {} as never);
      const task = {
        taskCode,
        contentConfig: { ...pendingConfig, contentStatus },
        evidenceSnapshot: {
          teacher_execution_variant: 'TEACHING_ENVIRONMENT_PHOTO',
        },
      };

      expect(resolve(repository)(task)).toBe(task);
      expect(task.contentConfig).not.toHaveProperty('contentVariant');
    },
  );

  it.each([
    ['P-FB-NEGATIVE', '灯光过暗'],
    ['P-FB-NEGATIVE', ' 灯光过暗/亮 '],
    ['P-FB-NEGATIVE', '环境杂乱/灯光差'],
    ['P-FB-COMPLAINT', '灯光过暗/亮'],
  ])('fails closed for task %s and non-exact label %s', (taskCode, label) => {
    const repository = new TaskRepository({} as never, {} as never);
    const task = {
      taskCode,
      contentConfig: pendingConfig,
      evidenceSnapshot: evidence(label),
    };

    expect(resolve(repository)(task)).toBe(task);
    expect(task.contentConfig.contentStatus).toBe('PENDING');
  });

  it('applies the resolver to normal task-list reads', async () => {
    const queryTide = jest.fn().mockResolvedValue({
      rows: [
        {
          taskCode: 'P-FB-NEGATIVE',
          contentConfig: pendingConfig,
          evidenceSnapshot: evidence('灯光过暗/亮'),
        },
      ],
    });
    const repository = new TaskRepository({ queryTide } as never, {} as never);
    const listRows = (
      repository as unknown as {
        listTaskRows(
          teacherId: string,
        ): Promise<Array<{ contentConfig: Record<string, unknown> }>>;
      }
    ).listTaskRows.bind(repository);

    const rows = await listRows('TEACHER-001');
    expect(rows).toHaveLength(1);
    expect(rows[0].contentConfig).toMatchObject({
      contentStatus: 'READY',
      contentVariant: 'TEACHING_ENVIRONMENT_PHOTO',
    });
  });

  it('uses the same resolver for the locked command read', async () => {
    const query = jest.fn().mockResolvedValue({
      rows: [
        {
          taskInstanceId: 'assignment-001',
          status: 'IN_PROGRESS',
          stateVersion: '7',
          executionVersionId: 'execution-001',
          contentConfig: pendingConfig,
          sourceType: 'PERSONALIZED_IMPROVEMENT',
          taskCode: 'P-FB-NEGATIVE',
          taskTitle: 'Improve a Teaching Skill',
          dataOrigin: 'REAL',
          teacherId: 'TEACHER-001',
          assignmentId: 'assignment-001',
          evidenceSnapshot: evidence('环境乱/灯光差'),
        },
      ],
    });
    const repository = new TaskRepository({} as never, {} as never);
    const lockOwnedTask = (
      repository as unknown as {
        lockOwnedTask(
          client: { query: typeof query },
          input: { accountId: string; taskInstanceId: string },
        ): Promise<{ contentConfig: Record<string, unknown> }>;
      }
    ).lockOwnedTask.bind(repository);

    await expect(
      lockOwnedTask(
        { query },
        { accountId: 'account-001', taskInstanceId: 'assignment-001' },
      ),
    ).resolves.toMatchObject({
      contentConfig: {
        contentStatus: 'READY',
        pendingReason: null,
        contentVariant: 'TEACHING_ENVIRONMENT_PHOTO',
      },
    });
    expect(query).toHaveBeenCalledWith(
      expect.stringContaining('task.evidence_snapshot AS "evidenceSnapshot"'),
      ['assignment-001', 'account-001'],
    );
  });

  it.each(['FAILED', 'UNDER_REVIEW'] as const)(
    'keeps a personalized photo %s result in progress so the teacher can retake it',
    async (status) => {
      const repository = new TaskRepository({} as never, {} as never);
      const updateAssignmentStatus = jest.fn();
      const createTaskResultNotification = jest.fn();
      Object.assign(repository as object, {
        updateAssignmentStatus,
        createTaskResultNotification,
      });
      const apply = (
        repository as unknown as {
          applyAssignmentValidationResult(
            client: Record<string, never>,
            task: Record<string, unknown>,
            input: {
              taskInstanceId: string;
              expectedStateVersion: number;
              attemptId: string;
            },
            decision: {
              status: 'FAILED' | 'UNDER_REVIEW';
              resultCode: string;
              teacherMessage: string;
              ruleVersion: string;
            },
          ): Promise<{ nextStatus: string; stateVersion: number }>;
        }
      ).applyAssignmentValidationResult.bind(repository);

      await expect(
        apply(
          {},
          {
            taskCode: 'P-FB-NEGATIVE',
            sourceType: 'PERSONALIZED_IMPROVEMENT',
            taskInstanceId: 'assignment-001',
            stateVersion: '7',
            contentConfig: {
              contentVariant: 'TEACHING_ENVIRONMENT_PHOTO',
            },
          },
          {
            taskInstanceId: 'assignment-001',
            expectedStateVersion: 7,
            attemptId: 'attempt-001',
          },
          {
            status,
            resultCode:
              status === 'FAILED'
                ? 'IMAGE_REVIEW_RETRY'
                : 'AI_GATEWAY_UNAVAILABLE',
            teacherMessage: '请重新拍照。',
            ruleVersion: 'photo-review:1',
          },
        ),
      ).resolves.toEqual({ nextStatus: 'IN_PROGRESS', stateVersion: 7 });
      expect(updateAssignmentStatus).not.toHaveBeenCalled();
      expect(createTaskResultNotification).not.toHaveBeenCalled();
    },
  );
});
