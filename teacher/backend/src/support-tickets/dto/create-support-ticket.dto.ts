import {
  IsIn,
  IsInt,
  IsObject,
  IsOptional,
  IsString,
  Max,
  MaxLength,
  Min,
  MinLength,
} from 'class-validator';
import { Transform, Type } from 'class-transformer';
import type { TransformFnParams } from 'class-transformer';
import type {
  SupportTicketLocation,
  SupportTicketSecondaryCategory,
} from '../support-ticket.models';

const secondaryCategories = [
  'TASK_RULES',
  'LESSON_INFO',
  'SCORE_OR_REVIEW',
  'PRODUCT_FUNCTION',
  'ACCOUNT_LOGIN',
  'MEDIA_UPLOAD_CAMERA',
  'OTHER',
] as const;

const problemLocations = [
  'MY_TIDE',
  'TASK',
  'LESSON',
  'MESSAGES',
  'ACCOUNT',
  'HELP',
  'OTHER',
] as const;

export class CreateSupportTicketDto {
  @IsIn(secondaryCategories)
  secondaryCategory!: SupportTicketSecondaryCategory;

  @IsIn(problemLocations)
  problemLocation!: SupportTicketLocation;

  @IsString()
  @MinLength(1)
  @MaxLength(5_000)
  description!: string;

  @IsOptional()
  @Transform(({ value }: TransformFnParams) => {
    if (typeof value !== 'string') return value as unknown;
    try {
      return JSON.parse(value) as unknown;
    } catch {
      return value;
    }
  })
  @IsObject()
  context?: Record<string, unknown>;
}

export class ReplySupportTicketDto {
  @IsString()
  @MinLength(1)
  @MaxLength(5_000)
  description!: string;

  @IsInt()
  @Type(() => Number)
  @Min(1)
  @Max(Number.MAX_SAFE_INTEGER)
  rowVersion!: number;
}

export class ResolveSupportTicketDto {
  @IsInt()
  @Type(() => Number)
  @Min(1)
  @Max(Number.MAX_SAFE_INTEGER)
  rowVersion!: number;
}
