import type { PoolClient } from 'pg';
import type { AppEventService } from '../app-events/app-event.service';
import type { AiGatewayService } from '../integrations/ai/ai-gateway.service';
import type { FaqMessage } from './faq.models';
import type { FaqRepository } from './faq.repository';
import { FaqRetriever } from './faq-retriever';
import { FaqService } from './faq.service';

const principal = { accountId: 'account-id', sessionId: 'session-id' };
const conversationId = 'e8211387-dc45-43af-a181-292670a11299';
const client = {} as PoolClient;

function createFixture() {
  let sequence = 0;
  const findCommandReplay = jest.fn().mockResolvedValue(null);
  const lockOwnedConversation = jest.fn().mockResolvedValue(undefined);
  const loadActiveKnowledge = jest.fn();
  const insertMessage = jest
    .fn()
    .mockImplementation(
      (
        _client: PoolClient,
        input: Omit<FaqMessage, 'id' | 'feedback' | 'createdAt'>,
      ) =>
        Promise.resolve({
          ...input,
          id: `message-${++sequence}`,
          feedback: null,
          createdAt: '2026-07-21T00:00:00.000Z',
        }),
    );
  const upsertGap = jest.fn().mockResolvedValue(undefined);
  const linkSources = jest.fn().mockResolvedValue(undefined);
  const saveCommandReceipt = jest.fn().mockResolvedValue(undefined);
  const loadActivePromptVersion = jest
    .fn()
    .mockResolvedValue('prompt-version-id');
  const getConversation = jest.fn();
  const transaction = jest.fn(
    (work: (transactionClient: PoolClient) => Promise<unknown>) => work(client),
  );
  const repository = {
    findCommandReplay,
    lockOwnedConversation,
    loadActiveKnowledge,
    insertMessage,
    upsertGap,
    linkSources,
    saveCommandReceipt,
    loadActivePromptVersion,
    transaction,
    getConversation,
  } as unknown as FaqRepository;
  const execute = jest.fn();
  const gateway = { execute } as unknown as AiGatewayService;
  const captureSystem = jest.fn<
    ReturnType<AppEventService['captureSystem']>,
    Parameters<AppEventService['captureSystem']>
  >();
  const service = new FaqService(repository, new FaqRetriever(), gateway, {
    captureSystem,
  } as unknown as AppEventService);
  return {
    service,
    captureSystem,
    execute,
    loadActiveKnowledge,
    insertMessage,
    upsertGap,
    linkSources,
    saveCommandReceipt,
    findCommandReplay,
    getConversation,
    repository,
  };
}

const approvedChunk = {
  id: 'chunk-id',
  title: 'Device FAQ',
  section: 'Microphone check',
  body: 'Test your microphone in the classroom settings before class.',
  metadata: {
    faqId: 'FAQ-TECH-001',
    question: 'How should I test my microphone before class?',
    category: 'Technical Support',
  },
};

describe('FaqService', () => {
  it('uses semantic catalog matching before recording a true FAQ gap', async () => {
    const fixture = createFixture();
    fixture.loadActiveKnowledge.mockResolvedValue([approvedChunk]);
    fixture.execute.mockResolvedValue({
      status: 'SUCCEEDED',
      aiRunId: 'intent-run-id',
      content: JSON.stringify({
        decision: 'NOT_FOUND',
        faqIds: [],
        clarificationQuestion: '',
      }),
    });

    await expect(
      fixture.service.ask(
        principal,
        conversationId,
        'faq-key-0001',
        {
          message: 'What will the weather be tomorrow?',
        },
        'browser-session-001',
      ),
    ).resolves.toMatchObject({
      answer: {
        body: 'I could not find a reliable answer in the current FAQ. Your question has been recorded for review. Submit a support ticket if you still need help.',
        faqHit: false,
        reasonCode: 'FAQ_NOT_FOUND',
      },
    });
    expect(fixture.execute).toHaveBeenCalledWith(
      expect.objectContaining({ capability: 'FAQ_INTENT_MATCH' }),
    );
    expect(fixture.upsertGap).toHaveBeenCalledTimes(1);
    expect(fixture.saveCommandReceipt).toHaveBeenCalledTimes(1);
    expect(fixture.captureSystem).toHaveBeenCalledWith(
      expect.objectContaining({
        eventName: 'FAQ_NOT_MATCHED',
        sessionId: 'browser-session-001',
        properties: {
          faqHit: false,
          result: 'NOT_MATCHED',
          reasonCode: 'FAQ_NOT_FOUND',
        },
      }),
    );
  });

  it('accepts a strict source-cited answer and stores only used sources', async () => {
    const fixture = createFixture();
    fixture.loadActiveKnowledge.mockResolvedValue([approvedChunk]);
    fixture.execute.mockResolvedValue({
      status: 'SUCCEEDED',
      aiRunId: 'run-id',
      content: JSON.stringify({
        decision: 'MATCHED',
        answer:
          '### Before class\n\n1. Open **classroom settings**. [Source 1]\n2. Test your microphone. [Source 1]',
        clarificationQuestion: '',
        usedSourcePositions: [1],
      }),
    });

    const response = await fixture.service.ask(
      principal,
      conversationId,
      'faq-key-0002',
      {
        message: 'How should I test my microphone before class?',
      },
      'browser-session-002',
    );
    expect(response).toMatchObject({
      answer: {
        body: '### Before class\n\n1. Open **classroom settings**.\n2. Test your microphone.',
        faqHit: true,
        reasonCode: 'FAQ_MATCHED',
      },
    });
    expect(response.answer).not.toHaveProperty('sources');
    expect(fixture.linkSources).toHaveBeenCalledTimes(1);
    expect(fixture.linkSources).toHaveBeenCalledWith(
      client,
      response.answer.id,
      [expect.objectContaining({ id: 'chunk-id', position: 1 })],
    );
    expect(fixture.upsertGap).not.toHaveBeenCalled();
    expect(fixture.execute).toHaveBeenCalledWith(
      expect.objectContaining({ promptVersionId: 'prompt-version-id' }),
    );
    expect(fixture.captureSystem).toHaveBeenCalledWith(
      expect.objectContaining({
        eventName: 'FAQ_MATCHED',
        sessionId: 'browser-session-002',
        properties: {
          faqHit: true,
          result: 'MATCHED',
          reasonCode: 'FAQ_MATCHED',
        },
      }),
    );
    expect(JSON.stringify(fixture.captureSystem.mock.calls)).not.toContain(
      'How should I test my microphone before class?',
    );
  });

  it('uses a safe source fallback when model output lacks citations', async () => {
    const fixture = createFixture();
    fixture.loadActiveKnowledge.mockResolvedValue([approvedChunk]);
    fixture.execute.mockResolvedValue({
      status: 'SUCCEEDED',
      aiRunId: 'run-id',
      content: JSON.stringify({
        decision: 'MATCHED',
        answer: 'An uncited answer.',
        clarificationQuestion: '',
        usedSourcePositions: [1],
      }),
    });

    await expect(
      fixture.service.ask(principal, conversationId, 'faq-key-0003', {
        message: 'How should I test my microphone before class?',
      }),
    ).resolves.toMatchObject({
      answer: {
        faqHit: false,
        reasonCode: 'FAQ_AI_RESPONSE_INVALID',
      },
    });
  });

  it('accepts strict JSON wrapped in a markdown code fence', async () => {
    const fixture = createFixture();
    fixture.loadActiveKnowledge.mockResolvedValue([approvedChunk]);
    fixture.execute.mockResolvedValue({
      status: 'SUCCEEDED',
      aiRunId: 'run-id',
      content:
        '```json\n{"decision":"MATCHED","answer":"Use classroom settings. [Source 1]","clarificationQuestion":"","usedSourcePositions":[1]}\n```',
    });

    await expect(
      fixture.service.ask(principal, conversationId, 'faq-key-0004', {
        message: 'How should I test my microphone before class?',
      }),
    ).resolves.toMatchObject({
      answer: { faqHit: true, reasonCode: 'FAQ_MATCHED' },
    });
  });

  it('uses full-catalog semantic matching when natural wording has no token overlap', async () => {
    const fixture = createFixture();
    fixture.loadActiveKnowledge.mockResolvedValue([approvedChunk]);
    fixture.execute
      .mockResolvedValueOnce({
        status: 'SUCCEEDED',
        aiRunId: 'intent-run-id',
        content: JSON.stringify({
          decision: 'MATCHED',
          faqIds: ['FAQ-TECH-001'],
          clarificationQuestion: '',
        }),
      })
      .mockResolvedValueOnce({
        status: 'SUCCEEDED',
        aiRunId: 'answer-run-id',
        content: JSON.stringify({
          decision: 'MATCHED',
          answer: 'Use the classroom settings to verify your audio. [Source 1]',
          clarificationQuestion: '',
          usedSourcePositions: [1],
        }),
      });

    const response = await fixture.service.ask(
      principal,
      conversationId,
      'faq-key-0005',
      {
        message: 'How do I make sure people can hear me?',
      },
    );
    expect(response).toMatchObject({
      answer: {
        faqHit: true,
        reasonCode: 'FAQ_MATCHED',
      },
    });
    expect(response.answer).not.toHaveProperty('sources');
    expect(fixture.execute).toHaveBeenNthCalledWith(
      1,
      expect.objectContaining({ capability: 'FAQ_INTENT_MATCH' }),
    );
    expect(fixture.execute).toHaveBeenNthCalledWith(
      2,
      expect.objectContaining({ capability: 'FAQ_TEXT_ANSWER' }),
    );
  });

  it('asks a follow-up question instead of guessing an ambiguous FAQ intent', async () => {
    const fixture = createFixture();
    fixture.loadActiveKnowledge.mockResolvedValue([approvedChunk]);
    fixture.execute.mockResolvedValue({
      status: 'SUCCEEDED',
      aiRunId: 'run-id',
      content: JSON.stringify({
        decision: 'CLARIFY',
        answer: '',
        clarificationQuestion:
          'Are you asking how to test your microphone before class?',
        usedSourcePositions: [],
      }),
    });

    await expect(
      fixture.service.ask(principal, conversationId, 'faq-key-0006', {
        message: 'I need help with my microphone',
      }),
    ).resolves.toMatchObject({
      answer: {
        body: 'Are you asking how to test your microphone before class?',
        faqHit: false,
        reasonCode: 'FAQ_CLARIFICATION_NEEDED',
      },
    });
    expect(fixture.upsertGap).not.toHaveBeenCalled();
  });

  it('retries against the full catalog when broad candidates are not relevant', async () => {
    const fixture = createFixture();
    fixture.loadActiveKnowledge.mockResolvedValue([approvedChunk]);
    fixture.execute
      .mockResolvedValueOnce({
        status: 'SUCCEEDED',
        aiRunId: 'candidate-run-id',
        content: JSON.stringify({
          decision: 'NOT_FOUND',
          answer: '',
          clarificationQuestion: '',
          usedSourcePositions: [],
        }),
      })
      .mockResolvedValueOnce({
        status: 'SUCCEEDED',
        aiRunId: 'intent-run-id',
        content: JSON.stringify({
          decision: 'MATCHED',
          faqIds: ['FAQ-TECH-001'],
          clarificationQuestion: '',
        }),
      })
      .mockResolvedValueOnce({
        status: 'SUCCEEDED',
        aiRunId: 'answer-run-id',
        content: JSON.stringify({
          decision: 'MATCHED',
          answer: 'Test it in classroom settings. [Source 1]',
          clarificationQuestion: '',
          usedSourcePositions: [1],
        }),
      });

    await expect(
      fixture.service.ask(principal, conversationId, 'faq-key-0008', {
        message: 'Can you help me test this before class?',
      }),
    ).resolves.toMatchObject({
      answer: { faqHit: true, reasonCode: 'FAQ_MATCHED' },
    });
    expect(fixture.execute).toHaveBeenCalledTimes(3);
  });

  it('does not present permissive candidates as approved sources when AI fails', async () => {
    const fixture = createFixture();
    fixture.loadActiveKnowledge.mockResolvedValue([approvedChunk]);
    fixture.execute.mockResolvedValue({
      status: 'FAILED',
      aiRunId: 'run-id',
      errorCode: 'AI_GATEWAY_UNAVAILABLE',
    });

    await expect(
      fixture.service.ask(principal, conversationId, 'faq-key-0007', {
        message: 'I need help with my microphone',
      }),
    ).resolves.toMatchObject({
      answer: {
        faqHit: false,
        reasonCode: 'FAQ_AI_UNAVAILABLE',
      },
    });
    expect(fixture.linkSources).not.toHaveBeenCalled();
  });

  it('removes legacy source details and source markers from replayed answers', async () => {
    const fixture = createFixture();
    fixture.findCommandReplay.mockResolvedValue({
      conversationId,
      teacherMessage: {
        id: 'teacher-message',
        role: 'TEACHER',
        body: 'How do I test my microphone?',
        faqHit: false,
        reasonCode: null,
        feedback: null,
        createdAt: '2026-07-21T00:00:00.000Z',
      },
      answer: {
        id: 'assistant-message',
        role: 'ASSISTANT',
        body: '**Open settings** and run the test. [Source 1]',
        faqHit: true,
        reasonCode: 'FAQ_MATCHED',
        sources: [{ position: 1, title: 'Legacy source' }],
        feedback: null,
        createdAt: '2026-07-21T00:00:01.000Z',
      },
    });

    const response = await fixture.service.ask(
      principal,
      conversationId,
      'faq-key-replay',
      { message: 'How do I test my microphone?' },
    );

    expect(response.answer.body).toBe('**Open settings** and run the test.');
    expect(response.answer).not.toHaveProperty('sources');
    expect(fixture.linkSources).not.toHaveBeenCalled();
  });

  it('keeps historical source links private when restoring a conversation', async () => {
    const fixture = createFixture();
    fixture.getConversation.mockResolvedValue({
      conversationId,
      status: 'OPEN',
      startedAt: '2026-07-21T00:00:00.000Z',
      messages: [
        {
          id: 'assistant-message',
          role: 'ASSISTANT',
          body: 'Use **classroom settings**. [Source 1]',
          faqHit: true,
          reasonCode: 'FAQ_MATCHED',
          sources: [{ position: 1, title: 'Legacy source' }],
          feedback: null,
          createdAt: '2026-07-21T00:00:01.000Z',
        },
      ],
    });

    const conversation = await fixture.service.getConversation(
      principal,
      conversationId,
    );

    expect(conversation.messages[0].body).toBe('Use **classroom settings**.');
    expect(conversation.messages[0]).not.toHaveProperty('sources');
  });
});
