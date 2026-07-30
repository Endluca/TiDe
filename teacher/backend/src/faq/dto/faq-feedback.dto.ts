import { IsBoolean, IsOptional, IsString, Length } from 'class-validator';

export class FaqFeedbackDto {
  @IsBoolean()
  resolved: boolean;

  @IsOptional()
  @IsString()
  @Length(1, 128)
  reasonCode?: string;
}
