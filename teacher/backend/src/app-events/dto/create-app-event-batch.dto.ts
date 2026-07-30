import { ArrayMaxSize, ArrayMinSize, IsArray, IsObject } from 'class-validator';
import type { CreateAppEventDto } from './create-app-event.dto';

export class CreateAppEventBatchDto {
  @IsArray()
  @ArrayMinSize(1)
  @ArrayMaxSize(50)
  @IsObject({ each: true })
  events: CreateAppEventDto[];
}
