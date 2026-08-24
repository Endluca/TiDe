import type { INestApplication } from '@nestjs/common';
import { Test } from '@nestjs/testing';
import type { Server } from 'node:http';
import { createHash, randomUUID } from 'node:crypto';
import { Pool } from 'pg';
import request from 'supertest';
import { AppModule } from '../src/app.module';
import type { AuthTokenPair } from '../src/auth/auth.models';
import type { UploadIntentResponse } from '../src/files/file.models';
import type {
  TaskContext,
  TaskListResponse,
  TaskMutationResponse,
} from '../src/tasks/task.models';
import type {
  CourseListResponse,
  G01ReviewResponse,
  NotificationListResponse,
} from '../src/tide/tide.models';
import type {
  OnboardingGuideStateResponse,
  OnboardingStateResponse,
} from '../src/onboarding/onboarding.models';
import {
  MailDeliveryAdapter,
  type PasswordResetEmailMessage,
  type VerificationEmailMessage,
} from '../src/integrations/mail/mail-delivery.adapter';

const runDatabaseIntegration =
  process.env.RUN_DATABASE_INTEGRATION === 'true' ? describe : describe.skip;

const TEST_EMAIL = 'shared-task-integration@51talk.test';
const TEST_TEACHER_ID = 'SHARED-TASK-INTEGRATION-TEACHER';
const TEST_IDENTITY_VIEW = 'tide.test_shiwen_teacher_identity_v1';

class RecordingMailAdapter extends MailDeliveryAdapter {
  messages: VerificationEmailMessage[] = [];
  passwordResetMessages: PasswordResetEmailMessage[] = [];

  sendVerificationEmail(message: VerificationEmailMessage): Promise<string> {
    this.messages.push(message);
    return Promise.resolve('integration-verification-message');
  }

  sendPasswordResetEmail(message: PasswordResetEmailMessage): Promise<string> {
    this.passwordResetMessages.push(message);
    return Promise.resolve('integration-reset-message');
  }
}

runDatabaseIntegration('shared database backend flow (e2e)', () => {
  let app: INestApplication;
  let database: Pool;
  let mail: RecordingMailAdapter;

  beforeAll(async () => {
    const databaseUrl = process.env.TEST_DATABASE_ADMIN_URL;
    if (!databaseUrl) throw new Error('TEST_DATABASE_ADMIN_URL is required');
    database = new Pool({ connectionString: databaseUrl });
    await cleanup(database);
    await prepareSharedTeacher(database);
    await assignFixedGrowthTasks(database);
    await createSafeViews(database);

    mail = new RecordingMailAdapter();
    const moduleFixture = await Test.createTestingModule({
      imports: [AppModule],
    })
      .overrideProvider(MailDeliveryAdapter)
      .useValue(mail)
      .compile();
    app = moduleFixture.createNestApplication();
    await app.init();
  });

  afterAll(async () => {
    if (app) await app.close();
    if (database) {
      await cleanup(database);
      await database.end();
    }
  });

  it('uses one shared assignment through tasks, files, G01, messages and events', async () => {
    const server = app.getHttpServer() as Server;
    const tokens = await registerAndLogin(server);
    await verifyFirstLoginOnboarding(server, tokens.accessToken, database);
    await assignPersonalizedEnvironmentTask(database);

    const taskListResponse = await request(server)
      .get('/api/v1/tasks')
      .set('authorization', `Bearer ${tokens.accessToken}`)
      .expect(200);
    const taskList = taskListResponse.body as TaskListResponse;
    expect(taskList.items).toHaveLength(10);
    expect(taskList.contexts).toHaveLength(10);
    expect(
      taskList.items.filter((item) => item.kind === 'FIXED_GROWTH'),
    ).toHaveLength(9);
    expect(
      taskList.items.filter((item) => item.kind === 'PERSONALIZED_IMPROVEMENT'),
    ).toHaveLength(1);
    expect(taskList.items.every((item) => item.status === 'ASSIGNED')).toBe(
      true,
    );
    expect(taskList.items.map((item) => item.taskCode).sort()).toEqual([
      'G01',
      'G02',
      'G03',
      'G04',
      'G05',
      'G06',
      'G07',
      'G08',
      'G09',
      'NT-Q03',
    ]);

    for (let readCount = 0; readCount < 5; readCount += 1) {
      await request(server)
        .get('/api/v1/tasks')
        .set('authorization', `Bearer ${tokens.accessToken}`)
        .expect(200);
    }
    const assignmentCount = await database.query<{ count: string }>(
      `SELECT count(*) FROM public.task_assignments
       WHERE teacher_id = $1 AND status = 'ASSIGNED'
         AND task_kind = 'FIXED_GROWTH'`,
      [TEST_TEACHER_ID],
    );
    expect(assignmentCount.rows[0].count).toBe('9');

    const g01Summary = taskList.items.find((item) => item.taskCode === 'G01')!;
    const g01ContextResponse = await request(server)
      .get(`/api/v1/tasks/${g01Summary.taskInstanceId}`)
      .set('authorization', `Bearer ${tokens.accessToken}`)
      .expect(200);
    const g01Context = g01ContextResponse.body as TaskContext;
    expect(g01Context.execution.contentStatus).toBe('READY');
    expect(g01Context.steps).toHaveLength(3);
    expect(
      g01Context.steps.filter((step) => step.type === 'QUIZ'),
    ).toHaveLength(1);
    expect(
      g01Context.steps.find((step) => step.type === 'QUIZ')?.config.questions,
    ).toHaveLength(61);
    expect(g01Context.capabilities).toEqual(
      expect.arrayContaining(['QUIZ', 'CHECKLIST', 'UPLOAD']),
    );

    const personalizedSummary = taskList.items.find(
      (item) => item.taskCode === 'NT-Q03',
    )!;
    expect(personalizedSummary.title).toBe('Check your next class setup');
    const personalizedContextResponse = await request(server)
      .get(`/api/v1/tasks/${personalizedSummary.taskInstanceId}`)
      .set('authorization', `Bearer ${tokens.accessToken}`)
      .expect(200);
    const personalizedContext = personalizedContextResponse.body as TaskContext;
    expect(personalizedContext.assignment).toMatchObject({
      teacherSafeFacts: [
        {
          label: 'What we noticed',
          value: 'A recent class had a connection issue.',
        },
      ],
      relatedCourses: [
        {
          lessonId: 'INTEGRATION-LESSON-001',
          label: 'Related class',
          summary: 'A recent class had a connection issue.',
        },
      ],
      reminderNotificationId: null,
    });

    const g03Pending = taskList.items.find((item) => item.taskCode === 'G03')!;
    const viewResponse = await request(server)
      .post(`/api/v1/tasks/${g03Pending.taskInstanceId}/view`)
      .set('authorization', `Bearer ${tokens.accessToken}`)
      .set('idempotency-key', 'shared-task-view-001')
      .send({
        commandId: 'shared-task-view-command-001',
        expectedStateVersion: g03Pending.stateVersion,
      })
      .expect(200);
    const viewed = viewResponse.body as TaskMutationResponse;
    expect(viewed).toMatchObject({ status: 'VIEWED', stateVersion: 2 });
    await request(server)
      .post(`/api/v1/tasks/${g03Pending.taskInstanceId}/view`)
      .set('authorization', `Bearer ${tokens.accessToken}`)
      .set('idempotency-key', 'shared-task-view-001')
      .send({
        commandId: 'shared-task-view-command-001',
        expectedStateVersion: g03Pending.stateVersion,
      })
      .expect(200)
      .expect(viewed);

    await request(server)
      .post(`/api/v1/tasks/${g03Pending.taskInstanceId}/start`)
      .set('authorization', `Bearer ${tokens.accessToken}`)
      .set('idempotency-key', 'shared-g03-start-001')
      .send({
        commandId: 'shared-g03-start-command-001',
        expectedStateVersion: viewed.stateVersion,
      })
      .expect(422)
      .expect((response) =>
        expect(response.body).toMatchObject({ code: 'CONTENT_NOT_READY' }),
      );

    const g02Policy = taskList.items.find((item) => item.taskCode === 'G02')!;
    const g02StartResponse = await request(server)
      .post(`/api/v1/tasks/${g02Policy.taskInstanceId}/start`)
      .set('authorization', `Bearer ${tokens.accessToken}`)
      .set('idempotency-key', 'shared-g02-start-001')
      .send({
        commandId: 'shared-g02-start-command-001',
        expectedStateVersion: g02Policy.stateVersion,
      })
      .expect(200);
    const g02Started = g02StartResponse.body as TaskMutationResponse;
    expect(g02Started).toMatchObject({
      status: 'IN_PROGRESS',
      stateVersion: 2,
    });
    await request(server)
      .post(`/api/v1/tasks/${g02Policy.taskInstanceId}/start`)
      .set('authorization', `Bearer ${tokens.accessToken}`)
      .set('idempotency-key', 'shared-g02-stale-start-001')
      .send({
        commandId: 'shared-g02-stale-start-command-001',
        expectedStateVersion: g02Policy.stateVersion,
      })
      .expect(409)
      .expect((response) =>
        expect((response.body as { code: string }).code).toBe(
          'STATE_VERSION_CONFLICT',
        ),
      );

    const g05Ttp = taskList.items.find((item) => item.taskCode === 'G05')!;
    const g05StartedResponse = await request(server)
      .post(`/api/v1/tasks/${g05Ttp.taskInstanceId}/start`)
      .set('authorization', `Bearer ${tokens.accessToken}`)
      .set('idempotency-key', 'shared-g05-start-001')
      .send({
        commandId: 'shared-g05-start-command-001',
        expectedStateVersion: g05Ttp.stateVersion,
      })
      .expect(200);
    const g05Started = g05StartedResponse.body as TaskMutationResponse;
    await database.query(
      `
        INSERT INTO tide.task_step_progress (
          id, task_assignment_id, step_key, status, percent,
          progress_summary, first_started_at, completed_at
        ) VALUES ($1, $2, 'g06-ttp-orientation-video', 'COMPLETED', 100,
          '{"mediaVersion":"g06-ttp-orientation-v1-01","resumeSeconds":314}'::jsonb,
          now(), now())
      `,
      [randomUUID(), g05Ttp.taskInstanceId],
    );
    await request(server)
      .put(`/api/v1/tasks/${g05Ttp.taskInstanceId}/progress`)
      .set('authorization', `Bearer ${tokens.accessToken}`)
      .set('idempotency-key', 'shared-g05-checklist-001')
      .send({
        commandId: 'shared-g05-checklist-command-001',
        expectedStateVersion: g05Started.stateVersion,
        stepKey: 'g06-learning-checklist',
        percent: 0,
        progress: {
          checkedItemKeys: Array.from(
            { length: 5 },
            (_item, index) => `item-${index + 1}`,
          ),
        },
      })
      .expect(200)
      .expect((response) => {
        expect(response.body).toMatchObject({
          status: 'IN_PROGRESS',
          stateVersion: g05Started.stateVersion,
          step: { status: 'COMPLETED', percent: 100 },
        });
      });
    const versionAfterProgress = await database.query<{ row_version: number }>(
      `SELECT row_version FROM public.task_assignments WHERE assignment_id = $1`,
      [g05Ttp.taskInstanceId],
    );
    expect(versionAfterProgress.rows[0].row_version).toBe(
      g05Started.stateVersion,
    );
    await request(server)
      .post(`/api/v1/tasks/${g05Ttp.taskInstanceId}/submissions`)
      .set('authorization', `Bearer ${tokens.accessToken}`)
      .set('idempotency-key', 'shared-g05-submit-001')
      .send({
        commandId: 'shared-g05-submit-command-001',
        expectedStateVersion: g05Started.stateVersion,
        attemptId: randomUUID(),
        outputs: [],
      })
      .expect(202)
      .expect((response) =>
        expect(response.body).toMatchObject({
          status: 'COMPLETED',
          stateVersion: 4,
          validation: { status: 'PASSED' },
        }),
      );

    const g04Readiness = taskList.items.find(
      (item) => item.taskCode === 'G04',
    )!;
    const fileContent = Buffer.from('shared assignment file intent');
    const fileSha = createHash('sha256').update(fileContent).digest('hex');
    const intent = await request(server)
      .post('/api/v1/files/upload-intents')
      .set('authorization', `Bearer ${tokens.accessToken}`)
      .set('idempotency-key', 'shared-file-intent-001')
      .send({
        taskInstanceId: g04Readiness.taskInstanceId,
        stepKey: 'g02-environment-photo',
        filename: 'environment.jpg',
        mimeType: 'image/jpeg',
        sizeBytes: fileContent.length,
        sha256: fileSha,
      })
      .expect(201);
    const intentBody = intent.body as UploadIntentResponse;
    const linkedIntent = await database.query<{ task_assignment_id: string }>(
      `SELECT task_assignment_id FROM tide.file_upload_intents WHERE file_id = $1`,
      [intentBody.fileId],
    );
    expect(linkedIntent.rows[0].task_assignment_id).toBe(
      g04Readiness.taskInstanceId,
    );

    await expectG01NotComplete(server, tokens.accessToken, database);
    await database.query(
      `
        UPDATE public.teacher_source_wide
        SET is_self_introduce = false, is_cpl_tesol = true
        WHERE tchr_id = $1
      `,
      [TEST_TEACHER_ID],
    );
    await request(server)
      .get('/api/v1/me/g01-review')
      .set('authorization', `Bearer ${tokens.accessToken}`)
      .expect(200)
      .expect((response) => {
        const body = response.body as G01ReviewResponse;
        expect(body).toMatchObject({
          tesolStatus: 'APPROVED',
          externalStatusesComplete: true,
        });
        expect(body).not.toHaveProperty('selfIntroStatus');
      });
    const g01 = await database.query<{
      status: string;
      completion_count: string;
    }>(
      `
        SELECT assignment.status,
          count(completion.id)::text AS completion_count
        FROM public.task_assignments assignment
        LEFT JOIN tide.task_completions completion
          ON completion.task_assignment_id = assignment.assignment_id
        WHERE assignment.teacher_id = $1 AND assignment.task_code = 'G01'
        GROUP BY assignment.assignment_id, assignment.status
      `,
      [TEST_TEACHER_ID],
    );
    expect(g01.rows[0]).toMatchObject({
      status: 'ASSIGNED',
      completion_count: '0',
    });

    await database.query(
      `
        INSERT INTO public.notifications (
          notification_id, task_id, teacher_id, channel, priority, status,
          payload
        ) VALUES (
          'INTEGRATION-NOTIFICATION-001', $1, $2, 'IN_APP', 'P1', 'STORED',
          '{"title":"Integration update","body":"Your latest business update is ready."}'::jsonb
        )
      `,
      // 本地历史 Mock Schema 仍要求 task_id；读模型必须忽略它并按纯文字消息展示。
      [g02Policy.taskInstanceId, TEST_TEACHER_ID],
    );
    await database.query(
      `
        INSERT INTO tide.system_notifications (
          system_notification_id, teacher_id, type_code, title, body,
          dedupe_key, payload
        ) VALUES (
          '91000000-0000-4000-8000-000000000001', $1,
          'INTEGRATION', 'System notice', 'System body',
          'integration:system:001', '{}'::jsonb
        )
      `,
      [TEST_TEACHER_ID],
    );
    await database.query(
      `
        INSERT INTO tide.system_notifications (
          system_notification_id, teacher_id, type_code, title, body,
          action_type, action_target, dedupe_key, payload
        ) VALUES (
          '91000000-0000-4000-8000-000000000002', $1,
          'GROWTH_STAGE_AVAILABLE',
          'Your next growth stage is ready',
          'A new set of required tasks is now available in your growth path.',
          'TASKS', '/path', 'integration:growth-stage:2',
          '{"stageNumber":2}'::jsonb
        )
      `,
      [TEST_TEACHER_ID],
    );
    const notifications = await request(server)
      .get('/api/v1/me/notifications')
      .set('authorization', `Bearer ${tokens.accessToken}`)
      .expect(200);
    const messages = (notifications.body as NotificationListResponse).items;
    expect(messages.map((message) => message.source).sort()).toEqual([
      'EXTERNAL',
      'SYSTEM',
      'SYSTEM',
      'SYSTEM',
      'SYSTEM',
    ]);
    expect(
      messages.find((message) => message.typeCode === 'GROWTH_STAGE_AVAILABLE'),
    ).toMatchObject({
      actionType: 'TASKS',
      actionTarget: '/path?stage=2',
      actionAvailable: true,
    });

    await request(server)
      .post('/api/v1/me/notifications/external:MOCK-NOTIFICATION-001/read')
      .set('authorization', `Bearer ${tokens.accessToken}`)
      .expect(404);
    const otherTeacherNotification = await database.query<{
      read_at: Date | null;
      clicked_at: Date | null;
    }>(
      `SELECT read_at, clicked_at FROM public.notifications WHERE notification_id = 'MOCK-NOTIFICATION-001'`,
    );
    expect(otherTeacherNotification.rows[0]).toMatchObject({
      read_at: null,
      clicked_at: null,
    });

    const externalId = 'external:INTEGRATION-NOTIFICATION-001';
    await request(server)
      .post(`/api/v1/me/notifications/${externalId}/read`)
      .set('authorization', `Bearer ${tokens.accessToken}`)
      .expect(204);
    await request(server)
      .post(`/api/v1/me/notifications/${externalId}/read`)
      .set('authorization', `Bearer ${tokens.accessToken}`)
      .expect(204);
    const externalEvents = await database.query<{ count: string }>(
      `
        SELECT count(*)
        FROM public.notification_events
        WHERE notification_id = 'INTEGRATION-NOTIFICATION-001'
          AND delivery_status = 'READ'
      `,
    );
    expect(externalEvents.rows[0].count).toBe('1');

    const businessStateBeforeEvent = await database.query<{
      status: string;
      row_version: number;
      score_count: string;
    }>(
      `
        SELECT assignment.status, assignment.row_version,
          (
            SELECT count(*)::text
            FROM public.score_entries score
            WHERE score.teacher_id = assignment.teacher_id
          ) AS score_count
        FROM public.task_assignments assignment
        WHERE assignment.assignment_id = $1
      `,
      [g04Readiness.taskInstanceId],
    );
    const appEventPayload = {
      eventName: 'TASK_CARD_CLICKED',
      eventId: 'integration-app-event-001',
      eventSchemaVersion: 1,
      sessionId: 'integration-browser-session-001',
      taskAssignmentId: g04Readiness.taskInstanceId,
      properties: {
        page: '/my-tide',
        entrySource: 'MY_TIDE',
        displayPosition: 'PRIMARY_TASK',
      },
      occurredAt: new Date().toISOString(),
    };
    await request(server)
      .post('/api/v1/app-events')
      .set('authorization', `Bearer ${tokens.accessToken}`)
      .send(appEventPayload)
      .expect(202)
      .expect({ accepted: true, eventId: 'integration-app-event-001' });
    await request(server)
      .post('/api/v1/app-events')
      .set('authorization', `Bearer ${tokens.accessToken}`)
      .send(appEventPayload)
      .expect(202);
    const appEvent = await database.query<{
      task_assignment_id: string;
      event_count: string;
      task_code: string;
      event_source: string;
    }>(
      `
        SELECT
          max(task_assignment_id) AS task_assignment_id,
          count(*)::text AS event_count,
          max(properties->>'taskCode') AS task_code,
          max(event_source) AS event_source
        FROM tide.app_events
        WHERE event_id = 'integration-app-event-001'
      `,
    );
    expect(appEvent.rows[0].task_assignment_id).toBe(
      g04Readiness.taskInstanceId,
    );
    expect(appEvent.rows[0]).toMatchObject({
      event_count: '1',
      task_code: 'G04',
      event_source: 'CLIENT',
    });
    const businessStateAfterEvent = await database.query<{
      status: string;
      row_version: number;
      score_count: string;
    }>(
      `
        SELECT assignment.status, assignment.row_version,
          (
            SELECT count(*)::text
            FROM public.score_entries score
            WHERE score.teacher_id = assignment.teacher_id
          ) AS score_count
        FROM public.task_assignments assignment
        WHERE assignment.assignment_id = $1
      `,
      [g04Readiness.taskInstanceId],
    );
    expect(businessStateAfterEvent.rows[0]).toEqual(
      businessStateBeforeEvent.rows[0],
    );

    await request(server)
      .post('/api/v1/app-events')
      .set('authorization', `Bearer ${tokens.accessToken}`)
      .send({
        eventName: 'TASK_CARD_CLICKED',
        eventId: 'integration-app-event-forged',
        eventSchemaVersion: 1,
        sessionId: 'integration-browser-session-001',
        taskAssignmentId: randomUUID(),
        properties: { page: '/my-tide' },
        occurredAt: new Date().toISOString(),
      })
      .expect(404);

    await request(server)
      .post('/api/v1/app-events')
      .set('authorization', `Bearer ${tokens.accessToken}`)
      .send({
        eventName: 'PAGE_VIEWED',
        eventId: 'integration-app-event-sensitive',
        eventSchemaVersion: 1,
        sessionId: 'integration-browser-session-001',
        properties: { email: 'must-not-be-stored@example.com' },
        occurredAt: new Date().toISOString(),
      })
      .expect(400);

    await request(server)
      .post('/api/v1/app-events/anonymous')
      .send({
        eventName: 'PAGE_VIEWED',
        eventId: 'integration-anonymous-event-001',
        eventSchemaVersion: 1,
        sessionId: 'integration-anonymous-session-001',
        properties: { page: '/sign-in', language: 'en' },
        occurredAt: new Date().toISOString(),
      })
      .expect(202);
    const anonymousEvent = await database.query<{
      teacher_binding_id: string | null;
      anonymous_teacher_id: string;
    }>(
      `
        SELECT teacher_binding_id, anonymous_teacher_id
        FROM tide.app_events
        WHERE event_id = 'integration-anonymous-event-001'
      `,
    );
    expect(anonymousEvent.rows[0].teacher_binding_id).toBeNull();
    expect(anonymousEvent.rows[0].anonymous_teacher_id).toMatch(
      /^[a-f0-9]{64}$/,
    );

    const courseResponse = await request(server)
      .get('/api/v1/me/courses')
      .set('authorization', `Bearer ${tokens.accessToken}`)
      .expect(200);
    const courseList = courseResponse.body as CourseListResponse;
    expect(courseList.items[0]).toMatchObject({
      lessonId:
        'participation:v1:WyJkb20iLCJJTlRFR1JBVElPTi1BUFBPSU5UTUVOVC0wMDEiLDFd',
      sourceRegion: 'dom',
      sourceAppointId: 'INTEGRATION-APPOINTMENT-001',
      participationSeq: 1,
      lifecycleStatus: 'end',
      scoreRuleVersion: 'integration-rule-v1',
    });
    expect(courseList.page).toBe(1);
    expect(courseList.totalCount).toBeGreaterThanOrEqual(1);
    expect(courseList.items[0].dimensions).toHaveLength(3);
    expect(courseList.items[0]).not.toHaveProperty('scoreExplanation');

    await request(server)
      .post('/api/v1/support/requests')
      .set('authorization', `Bearer ${tokens.accessToken}`)
      .send({})
      .expect(404);
  });

  async function registerAndLogin(server: Server): Promise<AuthTokenPair> {
    await request(server)
      .post('/api/v1/auth/register')
      .send({
        email: TEST_EMAIL,
        teacherId: TEST_TEACHER_ID,
        password: 'integration-password',
      })
      .expect(202);
    const token = new URL(mail.messages[0].verificationUrl).searchParams.get(
      'token',
    );
    await request(server)
      .post('/api/v1/auth/email-verification/confirm')
      .send({ token })
      .expect(200);
    const login = await request(server)
      .post('/api/v1/auth/login')
      .send({ email: TEST_EMAIL, password: 'integration-password' })
      .expect(200);
    return login.body as AuthTokenPair;
  }

  async function verifyFirstLoginOnboarding(
    server: Server,
    accessToken: string,
    connection: Pool,
  ): Promise<void> {
    await request(server)
      .get('/api/v1/me/onboarding')
      .expect(401)
      .expect((response) =>
        expect(response.body).toMatchObject({ code: 'AUTH_REQUIRED' }),
      );

    await request(server)
      .get('/api/v1/me/onboarding')
      .set('authorization', `Bearer ${accessToken}`)
      .expect(200)
      .expect((response) => {
        const state = response.body as OnboardingStateResponse;
        expect(state).toMatchObject({
          guideCode: 'FIRST_LOGIN',
          guideVersion: 1,
          required: true,
          status: null,
          acknowledgedAt: null,
        });
        expect(state.guides.map((guide) => guide.guideCode)).toEqual([
          'FIRST_LOGIN',
          'MY_TIDE_OVERVIEW',
          'SCORE_DETAILS',
          'TASK_PATH',
          'TASK_RESULT',
          'MESSAGES_TICKETS',
          'HELP_ROUTES',
          'PERSONALIZED_TASK_FIRST',
        ]);
      });

    await request(server)
      .post('/api/v1/me/onboarding/acknowledge')
      .set('authorization', `Bearer ${accessToken}`)
      .set('idempotency-key', 'onboarding-version-002')
      .send({
        guideCode: 'FIRST_LOGIN',
        guideVersion: 2,
        outcome: 'COMPLETED',
      })
      .expect(422)
      .expect((response) =>
        expect(response.body).toMatchObject({
          code: 'ONBOARDING_VERSION_UNSUPPORTED',
        }),
      );

    const requests = [
      {
        key: 'onboarding-completed-001',
        outcome: 'COMPLETED' as const,
      },
      { key: 'onboarding-skipped-001', outcome: 'SKIPPED' as const },
    ];
    const acknowledgements = await Promise.all(
      requests.map(({ key, outcome }) =>
        request(server)
          .post('/api/v1/me/onboarding/acknowledge')
          .set('authorization', `Bearer ${accessToken}`)
          .set('idempotency-key', key)
          .send({ guideCode: 'FIRST_LOGIN', guideVersion: 1, outcome })
          .expect(200),
      ),
    );
    const states = acknowledgements.map(
      (response) => response.body as OnboardingGuideStateResponse,
    );
    expect(states[0]).toMatchObject({
      guideCode: 'FIRST_LOGIN',
      guideVersion: 1,
      required: false,
    });
    expect(states[0].status).toMatch(/^(COMPLETED|SKIPPED)$/);
    expect(states[1]).toEqual(states[0]);

    const winner = requests.find(
      (candidate) => candidate.outcome === states[0].status,
    )!;
    await request(server)
      .post('/api/v1/me/onboarding/acknowledge')
      .set('authorization', `Bearer ${accessToken}`)
      .set('idempotency-key', winner.key)
      .send({
        guideCode: 'FIRST_LOGIN',
        guideVersion: 1,
        outcome: winner.outcome,
      })
      .expect(200)
      .expect(states[0]);
    await request(server)
      .post('/api/v1/me/onboarding/acknowledge')
      .set('authorization', `Bearer ${accessToken}`)
      .set('idempotency-key', winner.key)
      .send({
        guideCode: 'FIRST_LOGIN',
        guideVersion: 1,
        outcome: winner.outcome === 'COMPLETED' ? 'SKIPPED' : 'COMPLETED',
      })
      .expect(409)
      .expect((response) =>
        expect(response.body).toMatchObject({
          code: 'IDEMPOTENCY_KEY_REUSED',
        }),
      );

    const stored = await connection.query<{ count: string }>(
      `
        SELECT count(*)::text AS count
        FROM tide.account_onboarding_states state
        JOIN tide.user_accounts account ON account.id = state.account_id
        WHERE account.normalized_email = $1
          AND state.guide_code = 'FIRST_LOGIN'
          AND state.guide_version = 1
      `,
      [TEST_EMAIL],
    );
    expect(stored.rows[0].count).toBe('1');

    const nextLogin = await request(server)
      .post('/api/v1/auth/login')
      .send({ email: TEST_EMAIL, password: 'integration-password' })
      .expect(200);
    const nextTokens = nextLogin.body as AuthTokenPair;
    await request(server)
      .get('/api/v1/me/onboarding')
      .set('authorization', `Bearer ${nextTokens.accessToken}`)
      .expect(200)
      .expect((response) => {
        const state = response.body as OnboardingStateResponse;
        expect(state).toMatchObject(states[0]);
        expect(state.guides[0]).toEqual(states[0]);
      });
  }
});

async function expectG01NotComplete(
  server: Server,
  accessToken: string,
  database: Pool,
): Promise<void> {
  await request(server)
    .get('/api/v1/me/g01-review')
    .set('authorization', `Bearer ${accessToken}`)
    .expect(200)
    .expect((response) =>
      expect(response.body).toMatchObject({
        tesolStatus: 'WAITING',
        externalStatusesComplete: false,
      }),
    );
  const status = await database.query<{ status: string }>(
    `
      SELECT status FROM public.task_assignments
      WHERE teacher_id = $1 AND task_code = 'G01'
    `,
    [TEST_TEACHER_ID],
  );
  expect(status.rows[0].status).not.toBe('COMPLETED');
}

async function assignPersonalizedEnvironmentTask(
  database: Pool,
): Promise<void> {
  await database.query(
    `
      INSERT INTO public.task_assignments (
        teacher_id, task_code, template_version_id, task_kind,
        creator_system, priority, why, source_mode, dedupe_key, display_title,
        evidence_snapshot, due_at, timezone_used, timezone_source,
        timezone_verified_at
      )
      SELECT $1, 'NT-Q03', template.row_id, 'PERSONALIZED_IMPROVEMENT',
        'TRIGGER_CENTER', 'P2', 'Integration personalized assignment',
        'REAL', 'personalized:integration:NT-Q03',
        'Check your next class setup',
        jsonb_build_object(
          'lesson_ids', jsonb_build_array('INTEGRATION-LESSON-001'),
          'signal_samples', jsonb_build_array(jsonb_build_object(
            'lesson_id', 'INTEGRATION-LESSON-001',
            'why', 'A recent class had a connection issue.',
            'evidence', '{}'::jsonb
          ))
        ),
        now() + interval '12 hours',
        'Asia/Shanghai',
        'TEACHER_PROFILE',
        now()
      FROM public.task_templates template
      WHERE template.template_id = 'NT-Q03'
        AND template.status = 'PUBLISHED'
      ON CONFLICT (dedupe_key) DO NOTHING
    `,
    [TEST_TEACHER_ID],
  );
  await database.query(
    `
      INSERT INTO tide.system_notifications (
        system_notification_id, teacher_id, type_code, title, body,
        action_type, action_target, expires_at, dedupe_key, payload
      )
      SELECT
        '92000000-0000-4000-8000-000000000001',
        assignment.teacher_id,
        'PERSONALIZED_TASK_ASSIGNED',
        'Check your next class setup',
        'A new improvement task is ready for you. Open it when you are ready to take the next step.',
        'TASK_DETAIL',
        '/task/' || assignment.assignment_id,
        assignment.due_at,
        'personalized-task-assigned:' || assignment.assignment_id,
        jsonb_build_object(
          'taskAssignmentId', assignment.assignment_id,
          'reminderType', 'ASSIGNED'
        )
      FROM public.task_assignments assignment
      WHERE assignment.teacher_id = $1
        AND assignment.task_code = 'NT-Q03'
      ON CONFLICT (dedupe_key) DO NOTHING
    `,
    [TEST_TEACHER_ID],
  );
}

async function prepareSharedTeacher(database: Pool): Promise<void> {
  await database.query(
    `
      INSERT INTO public.teachers (
        teacher_id, camp_enrollment_id, name, timezone, camp_day,
        data_mode, payload
      ) VALUES ($1, 'SHARED-TASK-INTEGRATION-CAMP', 'Integration Teacher',
        'Asia/Shanghai', 30, 'REAL', '{}'::jsonb)
    `,
    [TEST_TEACHER_ID],
  );
  await database.query(
    `
      INSERT INTO public.teacher_source_wide (
        tchr_id, real_name, is_self_introduce, is_cpl_tesol
      ) VALUES ($1, 'Integration Teacher', true, false)
    `,
    [TEST_TEACHER_ID],
  );
  await database.query(
    `
      INSERT INTO public.teacher_metric_snapshots (
        snapshot_id, batch_id, teacher_id, snapshot_label, source_row_number,
        data_mode, is_self_introduce, is_cpl_tesol, raw_payload,
        metric_inputs, score_policy_snapshot, score_rule_version,
        user_feedback_score, reliability_score, class_quality_score,
        updated_at
      ) VALUES (
        'SHARED-TASK-INTEGRATION-SNAPSHOT', 'SHARED-TASK-INTEGRATION-BATCH',
        $1, 'Integration', 1, 'MIXED', true, false, '{}'::jsonb,
        '{
          "feedback_praise_cnt": 1,
          "feedback_favorite_cnt": 0,
          "completed_again_student_15d_cnt": 0,
          "on_time_completed_cnt": 1,
          "peak_completed_cnt": 0,
          "perfect_cnt": 0
        }'::jsonb,
        '{
          "scoring_items": {
            "feedback_praise": {"points_per_unit": 5},
            "feedback_favorite": {"points_per_unit": 5},
            "feedback_rebook_15d": {"points_per_unit": 8},
            "reliability_on_time": {"points_per_unit": 2},
            "reliability_peak": {"points_per_unit": 1},
            "classroom_quality": {"points_per_unit": 1.6}
          }
        }'::jsonb,
        'integration-rule-v1', 5, 2, 0, '2026-07-21T10:30:00Z'
      )
    `,
    [TEST_TEACHER_ID],
  );
  await database.query(
    `
      INSERT INTO public.lesson_facts (
        lesson_id, source_region, source_appoint_id, participation_seq,
        camp_enrollment_id, teacher_id,
        scheduled_start_at, scheduled_end_at, lesson_local_date,
        lesson_local_time, lesson_lifecycle_status, valid_for_scoring,
        evidence_status, data_mode, is_late, is_early,
        has_positive_feedback_tag, is_favorited,
        is_rebooked, is_camera_off, is_cpu_usage_high,
        is_network_delay_high, payload
      ) VALUES (
        'INTEGRATION-LESSON-001', 'dom', 'INTEGRATION-APPOINTMENT-001', 1,
        'SHARED-TASK-INTEGRATION-CAMP', $1,
        '2026-07-21T10:00:00Z', '2026-07-21T10:25:00Z',
        '2026-07-21', '10:00:00', 'end', true, 'CONFIRMED', 'REAL',
        false, false, true, false, false, false, false, false,
        '{}'::jsonb
      )
    `,
    [TEST_TEACHER_ID],
  );
}

async function assignFixedGrowthTasks(database: Pool): Promise<void> {
  await database.query(
    `
      INSERT INTO public.task_assignments (
        teacher_id, task_code, template_version_id, task_kind,
        creator_system, priority, why, source_mode, dedupe_key
      )
      SELECT
        $1::varchar,
        template.template_id,
        template.row_id,
        'FIXED_GROWTH',
        'TRIGGER_CENTER',
        COALESCE(NULLIF(template.payload ->> 'priority', ''), 'P1'),
        COALESCE(
          NULLIF(template.payload ->> 'why_template', ''),
          'Integration fixed growth assignment'
        ),
        'REAL',
        'fixed:' || $1::varchar || ':' || template.template_id
      FROM public.task_templates template
      WHERE template.status = 'PUBLISHED'
        AND template.template_id ~ '^G0[1-9]$'
      ON CONFLICT (dedupe_key) DO NOTHING
    `,
    [TEST_TEACHER_ID],
  );
}

async function createSafeViews(database: Pool): Promise<void> {
  await database.query(`
    CREATE VIEW ${TEST_IDENTITY_VIEW} AS
    SELECT '${TEST_TEACHER_ID}'::text AS teacher_id,
      'SHARED-TASK-INTEGRATION-CAMP'::text AS camp_enrollment_id,
      'Integration Teacher'::text AS name, 'Asia/Shanghai'::text AS timezone,
      30::integer AS camp_day, 'IN_CAMP'::text AS graduation_state,
      'REAL'::text AS data_mode, now() AS source_updated_at
  `);
}

async function cleanup(database: Pool): Promise<void> {
  await database.query(`DROP VIEW IF EXISTS ${TEST_IDENTITY_VIEW}`);

  const assignments = await database.query<{ assignment_id: string }>(
    `SELECT assignment_id FROM public.task_assignments WHERE teacher_id = $1`,
    [TEST_TEACHER_ID],
  );
  const assignmentIds = assignments.rows.map((row) => row.assignment_id);
  const account = await database.query<{ id: string }>(
    `SELECT id FROM tide.user_accounts WHERE normalized_email = $1`,
    [TEST_EMAIL],
  );
  const accountId = account.rows[0]?.id;

  await database.query('BEGIN');
  try {
    await database.query(
      `DELETE FROM public.notification_events WHERE notification_id IN (
        SELECT notification_id FROM public.notifications WHERE teacher_id = $1
      )`,
      [TEST_TEACHER_ID],
    );
    await database.query(
      `DELETE FROM public.notifications WHERE teacher_id = $1`,
      [TEST_TEACHER_ID],
    );
    await database.query(
      `DELETE FROM tide.system_notifications WHERE teacher_id = $1`,
      [TEST_TEACHER_ID],
    );
    await database.query(
      `DELETE FROM public.score_entries WHERE teacher_id = $1`,
      [TEST_TEACHER_ID],
    );
    if (assignmentIds.length > 0) {
      await database.query(
        `DELETE FROM tide.task_completions WHERE task_assignment_id = ANY($1::varchar[])`,
        [assignmentIds],
      );
      await database.query(
        `DELETE FROM tide.task_submissions WHERE task_assignment_id = ANY($1::varchar[])`,
        [assignmentIds],
      );
      await database.query(
        `DELETE FROM tide.task_attempts WHERE task_assignment_id = ANY($1::varchar[])`,
        [assignmentIds],
      );
    }
    if (accountId) {
      await database.query(
        `DELETE FROM tide.account_onboarding_states WHERE account_id = $1`,
        [accountId],
      );
      const files = await database.query<{ file_id: string }>(
        `SELECT file_id FROM tide.file_upload_intents WHERE account_id = $1`,
        [accountId],
      );
      await database.query(
        `DELETE FROM tide.file_upload_intents WHERE account_id = $1`,
        [accountId],
      );
      if (files.rows.length > 0) {
        await database.query(
          `DELETE FROM tide.file_objects WHERE id = ANY($1::uuid[])`,
          [files.rows.map((row) => row.file_id)],
        );
      }
      await database.query(
        `DELETE FROM tide.app_events WHERE teacher_binding_id IN (
          SELECT id FROM tide.teacher_bindings WHERE account_id = $1
        )`,
        [accountId],
      );
      await database.query(
        `DELETE FROM tide.source_read_status WHERE teacher_binding_id IN (
          SELECT id FROM tide.teacher_bindings WHERE account_id = $1
        )`,
        [accountId],
      );
      await database.query(
        `DELETE FROM tide.teacher_bindings WHERE account_id = $1`,
        [accountId],
      );
      await database.query(`DELETE FROM tide.user_accounts WHERE id = $1`, [
        accountId,
      ]);
    }
    if (assignmentIds.length > 0) {
      await database.query(
        `ALTER TABLE public.task_assignments DISABLE TRIGGER USER`,
      );
      await database.query(
        `DELETE FROM public.task_assignments WHERE assignment_id = ANY($1::varchar[])`,
        [assignmentIds],
      );
      await database.query(
        `ALTER TABLE public.task_assignments ENABLE TRIGGER USER`,
      );
      await database.query(
        `DELETE FROM public.audit_events WHERE task_id = ANY($1::varchar[])`,
        [assignmentIds],
      );
      await database.query(
        `DELETE FROM public.outbox_events WHERE aggregate_id = ANY($1::varchar[])`,
        [assignmentIds],
      );
    }
    await database.query(
      `DELETE FROM public.teacher_source_wide WHERE tchr_id = $1`,
      [TEST_TEACHER_ID],
    );
    await database.query(
      `DELETE FROM public.outbox_events
       WHERE aggregate_id = $1 AND event_type = 'source_wide.changed.v1'`,
      [TEST_TEACHER_ID],
    );
    await database.query(
      `DELETE FROM public.teacher_metric_snapshots WHERE teacher_id = $1`,
      [TEST_TEACHER_ID],
    );
    await database.query(
      `DELETE FROM public.lesson_facts WHERE teacher_id = $1`,
      [TEST_TEACHER_ID],
    );
    await database.query(`DELETE FROM public.teachers WHERE teacher_id = $1`, [
      TEST_TEACHER_ID,
    ]);
    await database.query('COMMIT');
  } catch (error) {
    await database.query('ROLLBACK');
    throw error;
  }
}
