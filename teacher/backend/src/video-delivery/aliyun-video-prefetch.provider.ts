import CdnClient, {
  DescribeRefreshTasksRequest,
  PushObjectCacheRequest,
} from '@alicloud/cdn20180510';
import { Config } from '@alicloud/openapi-client';
import {
  VideoPrefetchProvider,
  VideoPrefetchTaskGroup,
} from './video-prefetch.types';

function requiredEnvironment(name: string): string {
  const value = process.env[name]?.trim();
  if (!value) throw new Error(`缺少 CDN 环境变量：${name}`);
  return value;
}

export class AliyunVideoPrefetchProvider implements VideoPrefetchProvider {
  private readonly client: CdnClient;

  constructor(client?: CdnClient) {
    this.client =
      client ||
      new CdnClient(
        new Config({
          accessKeyId: requiredEnvironment('CDN_ACCESS_KEY_ID'),
          accessKeySecret: requiredEnvironment('CDN_ACCESS_KEY_SECRET'),
          regionId: process.env.CDN_REGION_ID?.trim() || 'ap-southeast-1',
          endpoint:
            process.env.CDN_API_ENDPOINT?.trim() ||
            'cdn.ap-southeast-1.aliyuncs.com',
        }),
      );
  }

  async submit(
    urls: string[],
    options: { area: 'overseas'; l2Preload: boolean },
  ): Promise<{ taskIds: string[]; requestId?: string }> {
    const response = await this.client.pushObjectCache(
      new PushObjectCacheRequest({
        objectPath: urls.join('\n'),
        area: options.area,
        l2Preload: options.l2Preload,
      }),
    );
    return {
      taskIds: (response.body?.pushTaskId || '')
        .split(',')
        .map((taskId) => taskId.trim())
        .filter(Boolean),
      requestId: response.body?.requestId,
    };
  }

  async describe(
    taskIds: string[],
    domainName: string,
  ): Promise<VideoPrefetchTaskGroup[]> {
    return Promise.all(
      taskIds.map(async (taskId) => {
        const response = await this.client.describeRefreshTasks(
          new DescribeRefreshTasksRequest({
            taskId,
            domainName,
            objectType: 'preload',
            pageSize: 100,
          }),
        );
        return {
          taskId,
          tasks: (response.body?.tasks?.CDNTask || []).map((task) => ({
            taskId: task.taskId || taskId,
            url: task.objectPath,
            status: task.status || 'Pending',
            process: task.process,
            creationTime: task.creationTime,
            description: task.description,
          })),
        };
      }),
    );
  }
}
