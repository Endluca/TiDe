import { TaskRepository } from './task.repository';
import type { TaskRelatedCourse, TaskTeacherSafeFact } from './task.models';

describe('TaskRepository step ordering', () => {
  const progressTask = (taskCode: string) => ({
    taskCode,
    taskInstanceId: 'assignment-001',
    executionVersionId: 'execution-001',
    contentConfig: {
      independentModules: {
        stepKeys: [
          'g02-device-check',
          'g02-environment-photo',
          'g02-courseware-confirmation',
        ],
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

  it.each([
    'g02-device-check',
    'g02-environment-photo',
    'g02-courseware-confirmation',
  ])('lets the configured G04 part %s save independently', async (stepKey) => {
    const query = jest.fn();
    const repository = new TaskRepository({} as never, {} as never);

    await expect(
      orderingCheck(repository)({ query }, progressTask('G04'), stepKey),
    ).resolves.toBeUndefined();
    expect(query).not.toHaveBeenCalled();
  });

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

describe('TaskRepository independent G04 submission state', () => {
  const g04Task = {
    taskCode: 'G04',
    taskInstanceId: 'assignment-001',
    stateVersion: '7',
    contentConfig: {
      independentModules: {
        stepKeys: [
          'g02-device-check',
          'g02-environment-photo',
          'g02-courseware-confirmation',
        ],
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
