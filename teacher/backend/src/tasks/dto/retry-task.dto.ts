import { IsOptional, IsString, MaxLength } from 'class-validator';
import { MutationMetaDto } from './mutation-meta.dto';

export class RetryTaskDto extends MutationMetaDto {
  @IsOptional()
  @IsString()
  @MaxLength(128)
  reasonCode?: string;
}
