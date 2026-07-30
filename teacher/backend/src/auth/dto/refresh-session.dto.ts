import { IsString, Length } from 'class-validator';

export class RefreshSessionDto {
  @IsString()
  @Length(32, 512)
  refreshToken: string;
}
