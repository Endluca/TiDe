import { Controller, Get, ServiceUnavailableException } from '@nestjs/common';
import {
  type DependencyStatus,
  HealthService,
  type HealthStatus,
  type ReadinessStatus,
} from './health.service';

@Controller('health')
export class HealthController {
  constructor(private readonly healthService: HealthService) {}

  @Get()
  getHealth(): HealthStatus {
    return this.healthService.getStatus();
  }

  @Get('ready')
  async getReadiness(): Promise<ReadinessStatus> {
    const readiness = await this.healthService.getReadiness();

    if (readiness.status !== 'ready') {
      throw new ServiceUnavailableException({
        code: 'SERVICE_NOT_READY',
        message: '服务依赖尚未就绪',
        retryable: true,
        details: readiness.checks,
      });
    }

    return readiness;
  }

  @Get('dependencies')
  getDependencies(): Promise<DependencyStatus> {
    return this.healthService.getDependencies();
  }
}
