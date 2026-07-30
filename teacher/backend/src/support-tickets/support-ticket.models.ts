import type { FileStorageProvider } from '../files/file-storage.adapter';

export type SupportTicketPrimaryCategory = 'OPERATIONS' | 'PRODUCT' | 'OTHER';
export type SupportTicketSecondaryCategory =
  | 'TASK_RULES'
  | 'LESSON_INFO'
  | 'SCORE_OR_REVIEW'
  | 'PRODUCT_FUNCTION'
  | 'ACCOUNT_LOGIN'
  | 'MEDIA_UPLOAD_CAMERA'
  | 'OTHER';
export type SupportTicketLocation =
  'MY_TIDE' | 'TASK' | 'LESSON' | 'MESSAGES' | 'ACCOUNT' | 'HELP' | 'OTHER';
export type SupportTicketStatus =
  'WAITING_OPERATOR' | 'WAITING_TEACHER' | 'CLOSED';

export interface SupportTicketImage {
  file_id?: string;
  object_key: string;
  filename: string;
  mime_type: string;
  size: number;
  storage_provider?: FileStorageProvider;
  deleted_at?: string | null;
}

export interface SupportTicketMessage {
  message_id: string;
  sender: 'TEACHER' | 'OPERATOR';
  content: string;
  images?: SupportTicketImage[];
  created_at: string;
}

export interface SupportTicketRow {
  ticketId: string;
  teacherId: string;
  primaryCategory: SupportTicketPrimaryCategory;
  secondaryCategory: SupportTicketSecondaryCategory;
  problemLocation: SupportTicketLocation;
  problemContext: Record<string, unknown>;
  messages: SupportTicketMessage[];
  status: SupportTicketStatus;
  lastOperatorReplyAt: Date | null;
  teacherReplyDeadlineAt: Date | null;
  lastReadOperatorMessageId: string | null;
  closeReason: 'RESOLVED' | 'NO_RESPONSE_TIMEOUT' | null;
  closedAt: Date | null;
  imageCleanupStatus: 'NOT_REQUIRED' | 'PENDING' | 'SUCCEEDED' | 'FAILED';
  rowVersion: string;
  createdAt: Date;
  updatedAt: Date;
}

export interface SupportTicketResponse {
  ticketId: string;
  primaryCategory: SupportTicketPrimaryCategory;
  secondaryCategory: SupportTicketSecondaryCategory;
  problemLocation: SupportTicketLocation;
  problemContext: Record<string, unknown>;
  messages: Array<{
    messageId: string;
    sender: 'TEACHER' | 'OPERATOR';
    content: string;
    createdAt: string;
    images: Array<{
      fileId: string;
      filename: string;
      mimeType: string;
      size: number;
      deleted: boolean;
      contentUrl: string | null;
    }>;
  }>;
  status: SupportTicketStatus;
  unread: boolean;
  lastOperatorReplyAt: string | null;
  teacherReplyDeadlineAt: string | null;
  closeReason: 'RESOLVED' | 'NO_RESPONSE_TIMEOUT' | null;
  closedAt: string | null;
  rowVersion: number;
  createdAt: string;
  updatedAt: string;
}
