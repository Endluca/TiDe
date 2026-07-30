import {
  IsInt,
  IsMimeType,
  IsString,
  Length,
  Matches,
  MaxLength,
  Min,
} from 'class-validator';

export class CreateUploadIntentDto {
  @IsString()
  @Length(1, 255)
  taskInstanceId: string;

  @IsString()
  @Length(1, 128)
  stepKey: string;

  @IsString()
  @Length(1, 255)
  filename: string;

  @IsMimeType()
  @MaxLength(255)
  mimeType: string;

  @IsInt()
  @Min(1)
  sizeBytes: number;

  @Matches(/^[0-9a-f]{64}$/)
  sha256: string;
}
