import type { FileStorageProvider } from './file-storage.adapter';

export interface UploadIntentResponse {
  fileId: string;
  uploadMethod: 'LOCAL_STREAM' | 'OSS_POST_FORM';
  uploadUrl: string;
  expiresAt: string;
  requiredHeaders: Record<string, string>;
  requiredFields: Record<string, string>;
}

export type FileObjectStatus = 'PENDING' | 'READY' | 'QUARANTINED' | 'DELETED';

export interface FileObjectResponse {
  fileId: string;
  status: FileObjectStatus;
  mimeType: string;
  sizeBytes: number;
  sha256: string;
}

export interface OwnedFileRecord extends FileObjectResponse {
  storageProvider: FileStorageProvider;
  objectKey: string;
  originalFilename: string;
  expiresAt: Date;
  uploadReceivedAt: Date | null;
}

export interface DownloadedFile {
  content: Buffer;
  mimeType: string;
  originalFilename: string;
}
