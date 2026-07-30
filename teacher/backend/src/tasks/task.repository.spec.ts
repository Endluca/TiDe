import { TaskRepository } from './task.repository';
import type { TaskRelatedCourse, TaskTeacherSafeFact } from './task.models';

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
