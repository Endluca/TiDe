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
} from './kuozhi-course.config';
import { KuozhiDetailClient } from './kuozhi-detail.client';
import {
  type KuozhiLaunchResponse,
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
    const configuredMapping = configuration.tasks[taskCode];
    if (!configuredMapping) {
      throw new NotFoundException({
        code: 'KUOZHI_COURSE_NOT_CONFIGURED',
        message: '当前任务尚未配置阔知课程',
        retryable: false,
      });
    }
    const { mappingVersion, ...mapping } = configuredMapping;
    return {
      mappingVersion,
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
            teacherId,
            targetUrl: target.toString(),
          }),
        };
      }),
    };
  }

  async fetchProgress(
    resolved: KuozhiResolvedMapping,
  ): Promise<KuozhiProgressCore> {
    const details = await Promise.all(
      resolved.mapping.courses.map((course) =>
        this.detailClient.getCourseDetail(
          resolved.queryTeacherId,
          course.courseId,
        ),
      ),
    );
    return evaluateKuozhiProgress(resolved, details, new Date().toISOString());
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
