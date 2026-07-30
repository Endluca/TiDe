import { IsString, Length } from 'class-validator';

export class ConfirmEmailDto {
  @IsString()
  @Length(32, 512)
  token: string;
}
