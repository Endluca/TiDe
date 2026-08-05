import {
  Injectable,
  NotFoundException,
  ServiceUnavailableException,
} from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import type { AppEnvironment } from '../../platform/config/environment';
import {
  loadKuozhiCourseConfiguration,
  type KuozhiCourseConfiguration,
  type KuozhiCourseMapping,
} from './kuozhi-course.config';
import { KuozhiDetailClient } from './kuozhi-detail.client';
import {
  kuozhiPassScoreKey,
  type KuozhiLaunchResponse,
  type KuozhiPassScoreReference,
  type KuozhiProgressCore,
  type KuozhiResolvedMapping,
} from './kuozhi.models';
import { evaluateKuozhiProgress } from './kuozhi-progress.evaluator';
import { createKuozhiTicketUrl } from './kuozhi-ticket';

@Injectable()
export class KuozhiService {
  private configurationPromise: Promise<KuozhiCourseConfiguration> | null =
    null;

  constructor(
    private readonly config: ConfigService<AppEnvironment, true>,
    private readonly detailClient: KuozhiDetailClient,
  ) {}

  async resolveMapping(
    taskCode: string,
    teacherId: string,
  ): Promise<KuozhiResolvedMapping> {
    const configuration = await this.configuration();
    const sampleMode = this.config.get('KUOZHI_SAMPLE_MODE', { infer: true });
    if (sampleMode && configuration.sampleProfile.taskCode === taskCode) {
      const queryTeacherId = this.config.get('KUOZHI_SAMPLE_TEACHER_ID', {
        infer: true,
      });
      if (!queryTeacherId) {
        throw new ServiceUnavailableException({
          code: 'KUOZHI_SAMPLE_NOT_CONFIGURED',
          message: '阔知示例账号尚未配置',
          retryable: false,
        });
      }
      const sample = configuration.sampleProfile;
      const mapping: KuozhiCourseMapping = {
        integrationStatus: sample.integrationStatus,
        launchEnabled: sample.launchEnabled,
        completionEnabled: sample.completionEnabled,
        noHeader: sample.noHeader,
        courses: sample.courses,
      };
      return {
        mappingVersion: configuration.version,
        taskCode,
        dataMode: 'SAMPLE_DRY_RUN',
        queryTeacherId,
        mapping,
      };
    }

    const mapping = configuration.tasks[taskCode];
    if (!mapping) {
      throw new NotFoundException({
        code: 'KUOZHI_COURSE_NOT_CONFIGURED',
        message: '当前任务尚未配置阔知课程',
        retryable: false,
      });
    }
    return {
      mappingVersion: configuration.version,
      taskCode,
      dataMode: 'REAL',
      queryTeacherId: teacherId,
      mapping,
    };
  }

  async createLaunch(
    taskCode: string,
    teacherId: string,
  ): Promise<KuozhiLaunchResponse> {
    const loginUrl = this.config.get('KUOZHI_LOGIN_URL', { infer: true });
    const courseUrl = this.config.get('KUOZHI_COURSE_URL', { infer: true });
    const appKey = this.config.get('KUOZHI_APP_KEY', { infer: true });
    const secretKey = this.config.get('KUOZHI_SECRET_KEY', { infer: true });
    if (!loginUrl || !courseUrl || !appKey || !secretKey) {
      throw new ServiceUnavailableException({
        code: 'KUOZHI_NOT_CONFIGURED',
        message: '课程服务暂未配置，请稍后重试',
        retryable: false,
      });
    }

    const resolved = await this.resolveMapping(taskCode, teacherId);
    if (!resolved.mapping.launchEnabled) {
      throw new NotFoundException({
        code: 'KUOZHI_LAUNCH_NOT_ENABLED',
        message: '当前任务未开放阔知课程入口',
        retryable: false,
      });
    }

    return {
      provider: 'KUOZHI',
      dataMode: resolved.dataMode,
      integrationStatus: resolved.mapping.integrationStatus,
      mappingVersion: resolved.mappingVersion,
      courses: resolved.mapping.courses.map((course) => {
        const target = new URL(`/course/${course.courseId}`, courseUrl);
        if (resolved.mapping.noHeader) target.searchParams.set('noheader', '1');
        return {
          courseId: course.courseId,
          title: course.title ?? null,
          embedMode: 'IFRAME',
          launchUrl: createKuozhiTicketUrl({
            loginUrl,
            appKey,
            secretKey,
            // Sample mode may use a separate teacher only for read-only
            // progress data. A login ticket must always represent the
            // authenticated TIDE teacher.
            teacherId,
            targetUrl: target.toString(),
          }),
        };
      }),
    };
  }

  passScoreReferences(
    mapping: KuozhiCourseMapping,
  ): KuozhiPassScoreReference[] {
    const references = mapping.courses.flatMap((course) =>
      course.tasks.flatMap((task) => {
        if (task.type !== 'TESTPAPER' || task.passScore.kind !== 'QUIZ_BANK') {
          return [];
        }
        return [
          {
            key: kuozhiPassScoreKey(
              task.passScore.bankKey,
              task.passScore.questionSetVersion,
            ),
            source: task.passScore,
          },
        ];
      }),
    );
    return [...new Map(references.map((item) => [item.key, item])).values()];
  }

  async fetchProgress(
    resolved: KuozhiResolvedMapping,
    publishedPassScores: ReadonlyMap<string, number>,
  ): Promise<KuozhiProgressCore> {
    const details = await Promise.all(
      resolved.mapping.courses.map((course) =>
        this.detailClient.getCourseDetail(
          resolved.queryTeacherId,
          course.courseId,
        ),
      ),
    );
    return evaluateKuozhiProgress(
      resolved,
      details,
      publishedPassScores,
      new Date().toISOString(),
    );
  }

  emptyProgress(resolved: KuozhiResolvedMapping): KuozhiProgressCore {
    return {
      provider: 'KUOZHI',
      dataMode: resolved.dataMode,
      integrationStatus: resolved.mapping.integrationStatus,
      mappingVersion: resolved.mappingVersion,
      syncStatus: 'NOT_SYNCED',
      refreshedAt: null,
      courses: resolved.mapping.courses.map((course) => ({
        courseId: course.courseId,
        title: course.title ?? `Course ${course.courseId}`,
        sourceAvailable: false,
        percent: null,
        completed: false,
        tasks: course.tasks.map((task) => ({
          courseTaskId: task.courseTaskId,
          title: task.title ?? `Task ${task.courseTaskId}`,
          type: task.type,
          required: task.required,
          sourceStatus: 'MISSING',
          percent: null,
          score: null,
          normalizedScorePercent: null,
          passScorePercent:
            task.type === 'TESTPAPER' && task.passScore.kind === 'FIXED'
              ? task.passScore.percent
              : null,
          testTimes: null,
          completed: false,
        })),
      })),
      completion: {
        enabled: resolved.mapping.completionEnabled,
        completed: false,
        reasonCode: 'NOT_SYNCED',
      },
    };
  }

  private configuration(): Promise<KuozhiCourseConfiguration> {
    this.configurationPromise ??= loadKuozhiCourseConfiguration(
      this.config.get('KUOZHI_COURSE_CONFIG_PATH', { infer: true }),
    );
    return this.configurationPromise;
  }
}
