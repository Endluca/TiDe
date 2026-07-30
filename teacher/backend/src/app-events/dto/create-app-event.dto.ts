import {
  Equals,
  IsDateString,
  IsInt,
  IsObject,
  IsOptional,
  IsString,
  Length,
} from 'class-validator';

export class CreateAppEventDto {
  @IsString()
  @Length(1, 128)
  eventName: string;

  @IsString()
  @Length(8, 128)
  eventId: string;

  @IsInt()
  @Equals(1)
  eventSchemaVersion: number;

  @IsString()
  @Length(8, 128)
  sessionId: string;

  @IsOptional()
  @IsString()
  @Length(1, 255)
  taskAssignmentId?: string;

  @IsObject()
  properties: Record<string, unknown>;

  @IsDateString()
  occurredAt: string;
}
