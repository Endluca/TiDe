import type { Request } from 'express';

export interface RequestWithId extends Request {
  id: string;
}
