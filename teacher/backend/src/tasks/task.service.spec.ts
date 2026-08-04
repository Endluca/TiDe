import { NotFoundException } from '@nestjs/common';
import type { AppEventService } from '../app-events/app-event.service';
import type { KuozhiService } from '../integrations/kuozhi/kuozhi.service';
import type { TaskRepository } from './task.repository';
import { TaskService } from './task.service';

function createFixture() {
  const findBinding = jest.fn().mockResolvedValue({
    bindingId: 'binding-001',
    teacherId: 'TEACHER-001',
  });
  const listTasksWithContexts = jest.fn().mockResolvedValue({
    items: [
      {
        taskInstanceId: 'assignment-001',
        taskCode: 'G01',
        kind: 'FIXED_GROWTH',
        status: 'ASSIGNED',
        stateVersion: 1,
        title: 'Profile',
        priority: 'P1',
        availableAt: null,
        dueAt: null,
        completedAt: null,
        dataOrigin: 'REAL',
      },
    ],
    contexts: [],
  });
  const viewTask = jest.fn().mockResolvedValue({
    accepted: true,
    taskInstanceId: 'assignment-001',
    status: 'VIEWED',
    stateVersion: 2,
  });
  const saveVideoHeartbeat = jest.fn().mockResolvedValue({
    accepted: true,
    taskInstanceId: 'assignment-001',
    status: 'IN_PROGRESS',
    stateVersion: 3,
    step: {
      stepKey: 'policy-video',
      status: 'IN_PROGRESS',
      percent: 20,
    },
  });
  const submitTask = jest.fn().mockResolvedValue({
    accepted: true,
    taskInstanceId: 'assignment-001',
    status: 'SUBMITTED',
    stateVersion: 4,
    validation: {
      status: 'FAILED',
      resultCode: 'QUIZ_BELOW_PASS_LINE',
    },
  });
  const isRetryAllowed = jest.fn().mockResolvedValue(true);
  const captureSystem = jest.fn<
    ReturnType<AppEventService['captureSystem']>,
    Parameters<AppEventService['captureSystem']>
  >();
  const repository = {
    findBinding,
    listTasksWithContexts,
    viewTask,
    saveVideoHeartbeat,
    submitTask,
    isRetryAllowed,
  } as unknown as TaskRepository;
  return {
    service: new TaskService(repository, {
      captureSystem,
    } as unknown as AppEventService),
    findBinding,
    listTasksWithContexts,
    viewTask,
    saveVideoHeartbeat,
    submitTask,
    isRetryAllowed,
    captureSystem,
  };
}

describe('TaskService', () => {
  it('lists assignments created in the shared database', async () => {
    const fixture = createFixture();

    await expect(
      fixture.service.list({
        accountId: 'account-001',
        sessionId: 'session-001',
      }),
    ).resolves.toEqual({
      items: [expect.objectContaining({ taskCode: 'G01', status: 'ASSIGNED' })],
      contexts: [],
    });
    expect(fixture.listTasksWithContexts).toHaveBeenCalledWith('TEACHER-001');
  });

  it('does not fall back to a local task copy when the binding is missing', async () => {
    const fixture = createFixture();
    fixture.findBinding.mockResolvedValue(null);

    await expect(
      fixture.service.list({
        accountId: 'account-001',
        sessionId: 'session-001',
      }),
    ).rejects.toBeInstanceOf(NotFoundException);
    expect(fixture.listTasksWithContexts).not.toHaveBeenCalled();
  });

  it('uses the authenticated account binding for the Kuozhi launch ticket', async () => {
    const repository = {
      findTask: jest.fn().mockResolvedValue({
        taskInstanceId: 'assignment-006',
        taskCode: 'G06',
      }),
      findBinding: jest.fn().mockResolvedValue({
        bindingId: 'binding-001',
        teacherId: 'TEACHER-001',
      }),
    } as unknown as TaskRepository;
    const createLaunch = jest.fn().mockResolvedValue({
      provider: 'KUOZHI',
      embedMode: 'IFRAME',
      courseId: '520',
      courseTaskId: '2791',
      launchUrl: 'https://login.example.test/ticket',
    });
    const service = new TaskService(repository, undefined, {
      createLaunch,
    } as unknown as KuozhiService);

    await expect(
      service.getKuozhiLaunch(
        { accountId: 'account-001', sessionId: 'session-001' },
        'assignment-006',
      ),
    ).resolves.toMatchObject({ courseId: '520' });
    expect(createLaunch).toHaveBeenCalledWith('G06', 'TEACHER-001');
  });

  it('marks a task viewed only through an explicit idempotent command', async () => {
    const fixture = createFixture();

    await expect(
      fixture.service.view(
        { accountId: 'account-001', sessionId: 'session-001' },
        'assignment-001',
        'view-command-key-001',
        { commandId: 'view-command-001', expectedStateVersion: 1 },
      ),
    ).resolves.toMatchObject({ status: 'VIEWED', stateVersion: 2 });
    expect(fixture.viewTask).toHaveBeenCalledWith(
      expect.objectContaining({
        accountId: 'account-001',
        taskInstanceId: 'assignment-001',
        expectedStateVersion: 1,
      }),
    );
  });

  it('saves video heartbeats without creating a task command', async () => {
    const fixture = createFixture();

    await expect(
      fixture.service.saveVideoHeartbeat(
        { accountId: 'account-001', sessionId: 'session-001' },
        'assignment-001',
        {
          stepKey: 'policy-video',
          positionSeconds: 3,
          playbackRate: 1,
        },
      ),
    ).resolves.toMatchObject({
      step: { stepKey: 'policy-video', percent: 20 },
    });
    expect(fixture.saveVideoHeartbeat).toHaveBeenCalledWith({
      accountId: 'account-001',
      taskInstanceId: 'assignment-001',
      stepKey: 'policy-video',
      positionSeconds: 3,
      playbackRate: 1,
    });
    expect(fixture.captureSystem).not.toHaveBeenCalled();
  });

  it('records backend completion with the browser analytics session', async () => {
    const fixture = createFixture();
    fixture.saveVideoHeartbeat.mockResolvedValue({
      accepted: true,
      taskInstanceId: 'assignment-001',
      status: 'IN_PROGRESS',
      stateVersion: 3,
      step: {
        stepKey: 'policy-video',
        status: 'COMPLETED',
        percent: 100,
      },
    });

    await fixture.service.saveVideoHeartbeat(
      { accountId: 'account-001', sessionId: 'auth-session-001' },
      'assignment-001',
      {
        stepKey: 'policy-video',
        positionSeconds: 120,
        playbackRate: 1,
      },
      'browser-session-001',
    );

    expect(fixture.captureSystem).toHaveBeenCalledWith(
      expect.objectContaining({
        eventName: 'VIDEO_COMPLETED',
        sessionId: 'browser-session-001',
        taskAssignmentId: 'assignment-001',
      }),
    );
  });

  it.each([
    [true, 'TASK_VALIDATION_RETRY_REQUIRED', 'RETRY_REQUIRED'],
    [false, 'TASK_VALIDATION_FAILED', 'FINAL_FAILURE'],
  ])(
    'distinguishes retryable and final validation failures',
    async (retryAllowed, eventName, result) => {
      const fixture = createFixture();
      fixture.isRetryAllowed.mockResolvedValue(retryAllowed);
      const expectedResult = String(result);

      await fixture.service.submit(
        { accountId: 'account-001', sessionId: 'auth-session-001' },
        'assignment-001',
        'submit-idempotency-key-001',
        {
          commandId: 'submit-command-001',
          expectedStateVersion: 3,
          attemptId: 'attempt-001',
          outputs: [],
        },
        'browser-session-001',
      );
      await new Promise((resolve) => setImmediate(resolve));

      expect(fixture.captureSystem).toHaveBeenCalledWith(
        expect.objectContaining({
          eventName,
          sessionId: 'browser-session-001',
          taskAssignmentId: 'assignment-001',
          properties: {
            result: expectedResult,
            errorCode: 'QUIZ_BELOW_PASS_LINE',
          },
        }),
      );
    },
  );
});
