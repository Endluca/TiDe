import { IsString, Length } from 'class-validator';

export class AskFaqDto {
  @IsString()
  @Length(2, 2_000)
  message: string;
}
