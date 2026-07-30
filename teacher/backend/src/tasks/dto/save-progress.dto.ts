import { IsInt, IsObject, IsString, Length, Max, Min } from 'class-validator';
import { MutationMetaDto } from './mutation-meta.dto';

export class SaveProgressDto extends MutationMetaDto {
  @IsString()
  @Length(1, 128)
  stepKey: string;

  @IsInt()
  @Min(0)
  @Max(100)
  percent: number;

  @IsObject()
  progress: Record<string, unknown>;
}
