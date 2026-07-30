import type { FileStorageProvider } from '../files/file-storage.adapter';

export type TeacherPhotoRunStatus =
  | 'UPLOADING'
  | 'CHECKING'
  | 'RETRY_REQUIRED'
  | 'BEAUTIFYING'
  | 'READY'
  | 'UNDER_REVIEW'
  | 'PROCESSING_FAILED';

export type TeacherPhotoDecision = 'PASS' | 'RETRY' | 'ERROR';

export interface TeacherPhotoCheck {
  id: string;
  title: string;
  status: 'pass' | 'fail' | 'uncertain';
  message: string;
  suggestion: string;
}

export interface TeacherPhotoRunResponse {
  photoRunId: string;
  status: TeacherPhotoRunStatus;
  decision: TeacherPhotoDecision | null;
  criteriaVersion: string;
  teacherMessage: string | null;
  submittedAt: string;
  checkedAt: string | null;
  checks: TeacherPhotoCheck[];
  finalPhoto: {
    fileId: string;
    contentUrl: string;
    filterPreset: string;
    filterStrength: number;
    savedAt: string;
  } | null;
}

export interface TeacherPhotoRunRecord {
  photoRunId: string;
  taskInstanceId: string;
  accountId: string;
  originalFileId: string;
  originalStorageProvider: FileStorageProvider;
  originalObjectKey: string;
  originalFilename: string;
  originalMimeType: string;
  finalFileId: string | null;
  finalStorageProvider: FileStorageProvider | null;
  finalObjectKey: string | null;
  status: TeacherPhotoRunStatus;
  decision: TeacherPhotoDecision | null;
  criteriaVersion: string;
  teacherMessage: string | null;
  checks: TeacherPhotoCheck[];
  filterPreset: string | null;
  filterStrength: number | null;
  submittedAt: Date;
  checkedAt: Date | null;
  processedAt: Date | null;
  processingOwner?: string | null;
  leaseExpiresAt?: Date | null;
  attemptCount?: number;
  nextAttemptAt?: Date | null;
}
