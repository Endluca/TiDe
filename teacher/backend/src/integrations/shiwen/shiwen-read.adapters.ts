import type {
  ShiwenLessonScore,
  ShiwenTeacherIdentity,
  ShiwenTeacherScorecard,
} from './shiwen-read.models';

export abstract class ShiwenTeacherReadAdapter {
  abstract findIdentity(
    teacherId: string,
  ): Promise<ShiwenTeacherIdentity | null>;

  abstract findScorecard(
    teacherId: string,
  ): Promise<ShiwenTeacherScorecard | null>;

  abstract listLessonScores(
    teacherId: string,
    options: { limit: number; offset: number; search?: string | null },
  ): Promise<ShiwenLessonScore[]>;
}
