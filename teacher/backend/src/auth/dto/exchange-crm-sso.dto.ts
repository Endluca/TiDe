import { IsString, MaxLength, MinLength } from 'class-validator';

export class ExchangeCrmSsoDto {
  @IsString()
  @MinLength(32)
  @MaxLength(128)
  code: string;
}
