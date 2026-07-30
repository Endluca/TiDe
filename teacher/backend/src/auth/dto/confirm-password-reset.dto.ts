import { IsString, Length } from 'class-validator';

export class ConfirmPasswordResetDto {
  @IsString()
  @Length(32, 512)
  token: string;

  @IsString()
  @Length(12, 128)
  newPassword: string;
}
