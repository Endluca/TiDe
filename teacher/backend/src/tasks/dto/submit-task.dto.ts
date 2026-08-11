import { Type } from 'class-transformer';
import {
  ArrayMaxSize,
  IsArray,
  IsIn,
  IsObject,
  IsString,
  IsUUID,
  Length,
  ValidateNested,
} from 'class-validator';
import { MutationMetaDto } from './mutation-meta.dto';

const outputTypes = [
  'CHECKLIST',
  'FILE',
  'DEVICE_CHECK',
  'EXTERNAL_PROOF',
  'DOCUMENT',
  'CUSTOM',
] as const;

export type TaskOutputType = (typeof outputTypes)[number];

export class StepOutputDto {
  @IsString()
  @Length(1, 128)
  stepKey: string;

  @IsIn(outputTypes)
  outputType: TaskOutputType;

  @IsObject()
  value: Record<string, unknown>;
}

export class SubmitTaskDto extends MutationMetaDto {
  @IsUUID()
  attemptId: string;

  @IsArray()
  @ArrayMaxSize(100)
  @ValidateNested({ each: true })
  @Type(() => StepOutputDto)
  outputs: StepOutputDto[];
}
