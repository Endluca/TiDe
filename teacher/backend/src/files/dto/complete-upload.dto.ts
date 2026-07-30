import { Matches } from 'class-validator';

export class CompleteUploadDto {
  @Matches(/^[0-9a-f]{64}$/)
  sha256: string;
}
