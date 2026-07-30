import { IsInt, IsString, Length, Min } from 'class-validator';

export class MutationMetaDto {
  @IsString()
  @Length(8, 128)
  commandId: string;

  @IsInt()
  @Min(1)
  expectedStateVersion: number;
}
