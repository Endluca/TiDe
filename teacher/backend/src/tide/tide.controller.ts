import {
  Controller,
  Get,
  Header,
  HttpCode,
  HttpStatus,
  Param,
  Post,
  Query,
  Req,
  UseGuards,
} from '@nestjs/common';
import {
  SessionAuthGuard,
  type AuthenticatedRequest,
} from '../auth/session-auth.guard';
import { TideService } from './tide.service';
import { CourseQueryDto } from './dto/course-query.dto';
import { NotificationQueryDto } from './dto/notification-query.dto';

@Controller('api/v1/me')
@UseGuards(SessionAuthGuard)
export class TideController {
  constructor(private readonly tide: TideService) {}

  @Get('profile')
  getProfile(@Req() request: AuthenticatedRequest) {
    return this.tide.getProfile(request.auth);
  }

  @Get('g01-review')
  getG01Review(@Req() request: AuthenticatedRequest) {
    return this.tide.getG01Review(request.auth);
  }

  @Get('tide-summary')
  @Header('Cache-Control', 'private, no-store')
  getSummary(@Req() request: AuthenticatedRequest) {
    return this.tide.getSummary(request.auth);
  }

  @Get('courses')
  getCourses(
    @Req() request: AuthenticatedRequest,
    @Query() query: CourseQueryDto,
  ) {
    return this.tide.getCourses(request.auth, query);
  }

  @Get('notifications')
  getNotifications(
    @Req() request: AuthenticatedRequest,
    @Query() query: NotificationQueryDto,
  ) {
    return this.tide.getNotifications(request.auth, query);
  }

  @Post('notifications/:sourceNotificationId/read')
  @HttpCode(HttpStatus.NO_CONTENT)
  async markNotificationRead(
    @Req() request: AuthenticatedRequest,
    @Param('sourceNotificationId') sourceNotificationId: string,
  ): Promise<void> {
    await this.tide.markNotificationRead(request.auth, sourceNotificationId);
  }

  @Post('notifications/:sourceNotificationId/click')
  @HttpCode(HttpStatus.NO_CONTENT)
  async markNotificationClicked(
    @Req() request: AuthenticatedRequest,
    @Param('sourceNotificationId') sourceNotificationId: string,
  ): Promise<void> {
    await this.tide.markNotificationClicked(request.auth, sourceNotificationId);
  }
}
