import {
  BadRequestException,
  Injectable,
  NotFoundException,
  ServiceUnavailableException,
} from '@nestjs/common';
import type { AuthPrincipal } from '../auth/auth.models';
import { ShiwenTeacherReadAdapter } from '../integrations/shiwen/shiwen-read.adapters';
import type {
  CourseListResponse,
  ExternalReviewStatus,
  G01ReviewResponse,
  NotificationListResponse,
  TeacherProfileResponse,
  TideSummaryResponse,
} from './tide.models';
import type { CourseQueryDto } from './dto/course-query.dto';
import type { NotificationQueryDto } from './dto/notification-query.dto';
import { TideRepository, type TeacherBindingIdentity } from './tide.repository';

const UNAVAILABLE_TASK_STATUSES = new Set([
  'COMPLETED',
  'EXPIRED',
  'WAIVED',
  'CANCELLED',
]);
const HIDDEN_FIXED_TASK_CODES = new Set(['G00']);

@Injectable()
export class TideService {
  constructor(
    private readonly repository: TideRepository,
    private readonly teacherReader: ShiwenTeacherReadAdapter,
  ) {}

  async getProfile(principal: AuthPrincipal): Promise<TeacherProfileResponse> {
    const binding = await this.requireBinding(principal.accountId);
    try {
      const identity = await this.teacherReader.findIdentity(binding.teacherId);
      if (!identity || identity.teacherId !== binding.teacherId) {
        throw new Error('INVALID_SOURCE_DATA');
      }
      await this.repository.recordSourceRead(
        binding.bindingId,
        'IDENTITY',
        true,
      );
      return {
        teacherId: binding.teacherId,
        email: binding.email,
        name: identity.name,
        timezone: identity.timezone,
        campDay: identity.campDay,
        totalCampDays: 30,
        graduationState: identity.graduationState,
        freshness: {
          source: 'LIVE',
          sourceUpdatedAt: identity.sourceUpdatedAt,
          fetchedAt: new Date().toISOString(),
          stale: false,
        },
      };
    } catch (error) {
      await this.repository.recordSourceRead(
        binding.bindingId,
        'IDENTITY',
        false,
        this.errorCode(error),
      );
      throw this.sourceUnavailable();
    }
  }

  async getG01Review(principal: AuthPrincipal): Promise<G01ReviewResponse> {
    const binding = await this.requireBinding(principal.accountId);
    try {
      const evidence = await this.repository.findLatestG01Evidence(
        binding.teacherId,
      );
      if (!evidence) throw new Error('G01_EVIDENCE_NOT_FOUND');

      const selfIntroStatus = this.mapEvidence(evidence.selfIntroduced);
      const tesolStatus = this.mapEvidence(evidence.tesolCompleted);
      const externalStatusesComplete =
        evidence.selfIntroduced === true && evidence.tesolCompleted === true;
      await this.repository.recordSourceRead(
        binding.bindingId,
        'G01_REVIEW',
        true,
      );
      return {
        selfIntroStatus,
        tesolStatus,
        externalStatusesComplete,
        freshness: {
          source: 'LIVE',
          sourceUpdatedAt: evidence.sourceUpdatedAt,
          fetchedAt: new Date().toISOString(),
          stale: false,
        },
      };
    } catch (error) {
      await this.repository.recordSourceRead(
        binding.bindingId,
        'G01_REVIEW',
        false,
        this.errorCode(error),
      );
      throw this.sourceUnavailable();
    }
  }

  async getSummary(principal: AuthPrincipal): Promise<TideSummaryResponse> {
    const binding = await this.requireBinding(principal.accountId);
    try {
      const [scorecard, fixedTasks] = await Promise.all([
        this.teacherReader.findScorecard(binding.teacherId),
        this.repository.listFixedGrowthTasks(binding.teacherId),
      ]);
      if (!scorecard || scorecard.teacherId !== binding.teacherId) {
        throw new Error('INVALID_SOURCE_DATA');
      }
      const taskDimension = scorecard.dimensions.find(
        (dimension) => dimension.code === 'NEW_TEACHER_TASK',
      );
      const taskComponentByCode = new Map(
        (taskDimension?.components ?? []).map((component) => [
          component.code,
          component,
        ]),
      );
      const availableItems = fixedTasks.flatMap((task) => {
        if (
          HIDDEN_FIXED_TASK_CODES.has(task.taskCode) ||
          UNAVAILABLE_TASK_STATUSES.has(task.status)
        ) {
          return [];
        }
        const component = taskComponentByCode.get(task.taskCode);
        const score = this.roundScore(component?.pointsPerUnit ?? 0);
        if (score <= 0) return [];
        return [
          {
            taskCode: task.taskCode,
            title: task.title,
            score,
            taskStatus: task.status,
          },
        ];
      });
      const availableScore = this.roundScore(
        availableItems.reduce((total, item) => total + item.score, 0),
      );
      await this.repository.recordSourceRead(
        binding.bindingId,
        'METRICS',
        true,
      );
      return {
        available: true,
        rawTotalScore: scorecard.rawTotalScore,
        publicTotalScore: scorecard.publicTotalScore,
        graduationState: scorecard.graduationState,
        graduationQualified: scorecard.graduationQualified,
        goldQualified: scorecard.goldQualified,
        graduationThreshold: scorecard.graduationThreshold,
        goldThreshold: scorecard.goldThreshold,
        mandatoryTaskCompletedCount: scorecard.mandatoryTaskCompletedCount,
        mandatoryTaskTotalCount: scorecard.mandatoryTaskTotalCount,
        scoreRuleVersion: scorecard.scoreRuleVersion,
        calculatedAt: scorecard.calculatedAt,
        dimensions: scorecard.dimensions.map((dimension) => ({
          code: dimension.code,
          score: dimension.score,
          scoreRuleVersion: dimension.scoreRuleVersion,
          projectionRevision: dimension.projectionRevision,
          calculatedAt: dimension.calculatedAt,
          components: dimension.components.map((component) => ({
            code: component.code,
            unitCount: component.unitCount,
            pointsPerUnit: component.pointsPerUnit,
            score: component.score,
            sourceScope: component.sourceScope,
            sourceMetric: component.sourceMetric,
          })),
        })),
        availableScore:
          availableScore > 0
            ? { score: availableScore, items: availableItems }
            : null,
        freshness: {
          source: 'LIVE',
          sourceUpdatedAt: scorecard.calculatedAt,
          fetchedAt: new Date().toISOString(),
          stale: false,
        },
      };
    } catch (error) {
      await this.repository.recordSourceRead(
        binding.bindingId,
        'METRICS',
        false,
        this.errorCode(error),
      );
      throw this.sourceUnavailable();
    }
  }

  private roundScore(value: number): number {
    return Math.round((value + Number.EPSILON) * 100) / 100;
  }

  async getCourses(
    principal: AuthPrincipal,
    query: CourseQueryDto,
  ): Promise<CourseListResponse> {
    const binding = await this.requireBinding(principal.accountId);
    try {
      const courses = await this.teacherReader.listLessonScores(
        binding.teacherId,
        {
          limit: query.pageSize,
          offset: (query.page - 1) * query.pageSize,
          search: query.search?.trim() || null,
        },
      );
      const safeCourses = courses.filter(
        (course) => course.teacherId === binding.teacherId,
      );
      await this.repository.recordSourceRead(
        binding.bindingId,
        'COURSES',
        true,
      );
      return {
        items: safeCourses.map((course) => ({
          lessonId: course.lessonId,
          lessonSequence: course.lessonSequence,
          lessonCount: course.lessonCount,
          scheduledStartAt: course.scheduledStartAt,
          lessonLocalDate: course.lessonLocalDate,
          lessonLocalTime: course.lessonLocalTime,
          lifecycleStatus: course.lifecycleStatus,
          validForScoring: course.validForScoring,
          evidenceStatus: course.evidenceStatus,
          lessonTotalScore: course.lessonTotalScore,
          scoreRuleVersion: course.scoreRuleVersion,
          updatedAt: course.updatedAt,
          facts: course.facts,
          dimensions: course.dimensions,
        })),
        page: query.page,
        pageSize: query.pageSize,
        totalCount: safeCourses[0]?.lessonCount ?? 0,
        freshness: {
          source: 'LIVE',
          sourceUpdatedAt: this.latestTimestamp(
            safeCourses.map((course) => course.updatedAt),
          ),
          fetchedAt: new Date().toISOString(),
          stale: false,
        },
      };
    } catch (error) {
      await this.repository.recordSourceRead(
        binding.bindingId,
        'COURSES',
        false,
        this.errorCode(error),
      );
      throw this.sourceUnavailable();
    }
  }

  async getNotifications(
    principal: AuthPrincipal,
    query: NotificationQueryDto,
  ): Promise<NotificationListResponse> {
    const binding = await this.requireBinding(principal.accountId);
    const cursor = this.decodeNotificationCursor(query.cursor);
    const page = await this.repository.listNotifications(binding.teacherId, {
      limit: query.limit,
      unreadOnly: query.filter === 'UNREAD',
      beforeIssuedAt: cursor?.issuedAt ?? null,
      beforeSourceNotificationId: cursor?.sourceNotificationId ?? null,
    });
    const last = page.items.at(-1);
    return {
      items: page.items,
      totalCount: page.totalCount,
      unreadCount: page.unreadCount,
      nextCursor:
        page.hasMore && last
          ? this.encodeNotificationCursor(
              last.issuedAt,
              last.sourceNotificationId,
            )
          : null,
      freshness: {
        source: 'LIVE',
        sourceUpdatedAt: null,
        fetchedAt: new Date().toISOString(),
        stale: false,
      },
    };
  }

  private encodeNotificationCursor(
    issuedAt: string,
    sourceNotificationId: string,
  ): string {
    return Buffer.from(
      JSON.stringify({ issuedAt, sourceNotificationId }),
      'utf8',
    ).toString('base64url');
  }

  private decodeNotificationCursor(cursor: string | undefined): {
    issuedAt: Date;
    sourceNotificationId: string;
  } | null {
    if (!cursor) return null;
    try {
      const parsed = JSON.parse(
        Buffer.from(cursor, 'base64url').toString('utf8'),
      ) as { issuedAt?: unknown; sourceNotificationId?: unknown };
      if (
        typeof parsed.issuedAt !== 'string' ||
        typeof parsed.sourceNotificationId !== 'string' ||
        !parsed.sourceNotificationId ||
        Number.isNaN(Date.parse(parsed.issuedAt))
      ) {
        throw new Error('invalid cursor');
      }
      return {
        issuedAt: new Date(parsed.issuedAt),
        sourceNotificationId: parsed.sourceNotificationId,
      };
    } catch {
      throw new BadRequestException({
        code: 'INVALID_NOTIFICATION_CURSOR',
        message: '消息分页游标无效',
        retryable: false,
      });
    }
  }

  async markNotificationRead(
    principal: AuthPrincipal,
    sourceNotificationId: string,
  ): Promise<void> {
    const binding = await this.requireBinding(principal.accountId);
    const marked = await this.repository.markNotificationRead(
      binding.teacherId,
      sourceNotificationId,
    );
    if (!marked) throw this.notificationNotFound();
  }

  async markNotificationClicked(
    principal: AuthPrincipal,
    sourceNotificationId: string,
  ): Promise<void> {
    const binding = await this.requireBinding(principal.accountId);
    const marked = await this.repository.markNotificationClicked(
      binding.teacherId,
      sourceNotificationId,
    );
    if (!marked) throw this.notificationNotFound();
  }

  private async requireBinding(
    accountId: string,
  ): Promise<TeacherBindingIdentity> {
    const binding = await this.repository.findBinding(accountId);
    if (!binding) {
      throw new ServiceUnavailableException({
        code: 'ACCOUNT_BINDING_UNAVAILABLE',
        message: '教师账号绑定暂时不可用',
        retryable: true,
      });
    }
    return binding;
  }

  private mapEvidence(value: boolean | null): ExternalReviewStatus {
    if (value === true) return 'APPROVED';
    if (value === false) return 'WAITING';
    return 'UNAVAILABLE';
  }

  private notificationNotFound(): NotFoundException {
    return new NotFoundException({
      code: 'RESOURCE_NOT_FOUND',
      message: '通知不存在或已失效',
      retryable: false,
    });
  }

  private sourceUnavailable(): ServiceUnavailableException {
    return new ServiceUnavailableException({
      code: 'SOURCE_UNAVAILABLE',
      message: '所需数据暂时不可用，请稍后重试',
      retryable: true,
    });
  }

  private errorCode(error: unknown): string {
    return error instanceof Error ? error.name : 'UNKNOWN_SOURCE_ERROR';
  }

  private latestTimestamp(
    values: Array<string | null | undefined>,
  ): string | null {
    return (
      values
        .filter((value): value is string => Boolean(value))
        .sort()
        .at(-1) ?? null
    );
  }
}
