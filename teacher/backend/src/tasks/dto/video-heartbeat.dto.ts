import { IsInt, IsNumber, IsString, Length, Min } from 'class-validator';

export class VideoHeartbeatDto {
  @IsString()
  @Length(1, 128)
  stepKey: string;

  @IsInt()
  @Min(0)
  positionSeconds: number;

  @IsNumber()
  playbackRate: number;
}
