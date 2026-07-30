import type { AuthPrincipal } from '../auth/auth.models';
import type { FileStorageAdapter } from '../files/file-storage.adapter';
import type {
  SupportTicketMessage,
  SupportTicketRow,
} from './support-ticket.models';
import type { SupportTicketRepository } from './support-ticket.repository';
import { SupportTicketService } from './support-ticket.service';

const ticketId = '41000000-0000-4000-8000-000000000001';
const principal: AuthPrincipal = {
  accountId: 'account-001',
  sessionId: 'session-001',
};

const messages: SupportTicketMessage[] = [
  {
    message_id: '41000000-0000-4000-8000-000000000011',
    sender: 'TEACHER',
    content: 'Teacher screenshot',
    created_at: '2026-07-28T00:00:00.000Z',
    images: [
      {
        file_id: '41000000-0000-4000-8000-000000000021',
        object_key:
          'support-tickets/41000000-0000-4000-8000-000000000001/41000000-0000-4000-8000-000000000011/teacher.png',
        filename: 'teacher.png',
        mime_type: 'image/png',
        size: 10,
        storage_provider: 'OSS',
      },
    ],
  },
  {
    message_id: '41000000-0000-4000-8000-000000000012',
    sender: 'OPERATOR',
    content: 'Operator screenshot',
    created_at: '2026-07-28T00:05:00.000Z',
    images: [
      {
        file_id: '41000000-0000-4000-8000-000000000022',
        object_key:
          'support-tickets/41000000-0000-4000-8000-000000000001/41000000-0000-4000-8000-000000000012/operator.webp',
        filename: 'operator.webp',
        mime_type: 'image/webp',
        size: 20,
        storage_provider: 'OSS',
      },
    ],
  },
];

const closedTicket: SupportTicketRow = {
  ticketId,
  teacherId: 'teacher-001',
  primaryCategory: 'PRODUCT',
  secondaryCategory: 'PRODUCT_FUNCTION',
  problemLocation: 'HELP',
  problemContext: {},
  messages,
  status: 'CLOSED',
  lastOperatorReplyAt: new Date('2026-07-28T00:05:00.000Z'),
  teacherReplyDeadlineAt: null,
  lastReadOperatorMessageId: null,
  closeReason: 'RESOLVED',
  closedAt: new Date('2026-07-28T00:10:00.000Z'),
  imageCleanupStatus: 'PENDING',
  rowVersion: '3',
  createdAt: new Date('2026-07-28T00:00:00.000Z'),
  updatedAt: new Date('2026-07-28T00:10:00.000Z'),
};

describe('SupportTicketService', () => {
  it('keeps multi-selected task and lesson context while preserving singular fields', async () => {
    const createTicket = jest.fn().mockResolvedValue(closedTicket);
    const repository = {
      create: createTicket,
    } as unknown as SupportTicketRepository;
    const storage = {
      activeProvider: 'OSS',
    } as unknown as FileStorageAdapter;
    const service = new SupportTicketService(repository, storage);

    await service.create(
      principal,
      {
        secondaryCategory: 'TASK_RULES',
        problemLocation: 'HELP',
        description: 'Two tasks have the same issue.',
        context: {
          taskAssignmentId: 'assignment-001',
          taskAssignmentIds: ['assignment-001', 'assignment-002', 42],
          taskCodes: ['G01', 'G02'],
          taskNames: ['Profile', 'Lesson preparation'],
          lessonIds: ['lesson-001', 'lesson-002'],
          ignoredValues: ['internal'],
        },
      },
      undefined,
      undefined,
    );

    expect(createTicket).toHaveBeenCalledWith(
      expect.objectContaining({
        context: {
          taskAssignmentId: 'assignment-001',
          taskAssignmentIds: ['assignment-001', 'assignment-002'],
          taskCodes: ['G01', 'G02'],
          taskNames: ['Profile', 'Lesson preparation'],
          lessonIds: ['lesson-001', 'lesson-002'],
        },
      }),
    );
  });

  it('deletes both teacher and operator images when TIDE closes a ticket', async () => {
    const resolveTicket = jest.fn().mockResolvedValue(closedTicket);
    const markImagesDeleted = jest.fn().mockResolvedValue(undefined);
    const findOwned = jest.fn().mockResolvedValue({
      ...closedTicket,
      imageCleanupStatus: 'SUCCEEDED',
    });
    const repository = {
      resolve: resolveTicket,
      markImagesDeleted,
      findOwned,
    } as unknown as SupportTicketRepository;
    const deleteImage = jest.fn().mockResolvedValue(undefined);
    const storage = {
      activeProvider: 'OSS',
      delete: deleteImage,
    } as unknown as FileStorageAdapter;
    const service = new SupportTicketService(repository, storage);

    await service.resolve(principal, ticketId, 2);

    expect(deleteImage).toHaveBeenCalledTimes(2);
    expect(deleteImage).toHaveBeenNthCalledWith(
      1,
      messages[0].images?.[0].object_key,
      'OSS',
    );
    expect(deleteImage).toHaveBeenNthCalledWith(
      2,
      messages[1].images?.[0].object_key,
      'OSS',
    );
    expect(markImagesDeleted).toHaveBeenCalledWith(ticketId, [
      messages[0].images?.[0].object_key,
      messages[1].images?.[0].object_key,
    ]);
  });
});
