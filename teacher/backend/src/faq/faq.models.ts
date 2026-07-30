export interface FaqFeedback {
  resolved: boolean;
  reasonCode: string | null;
}

export interface FaqMessage {
  id: string;
  role: 'TEACHER' | 'ASSISTANT' | 'SYSTEM';
  body: string;
  faqHit: boolean;
  reasonCode: string | null;
  feedback: FaqFeedback | null;
  createdAt: string;
}

export interface FaqConversation {
  conversationId: string;
  status: 'OPEN' | 'CLOSED' | 'ARCHIVED';
  startedAt: string;
  messages: FaqMessage[];
}

export interface FaqConversationCreated {
  conversationId: string;
  status: 'OPEN';
  startedAt: string;
}

export interface FaqAnswerResponse {
  conversationId: string;
  teacherMessage: FaqMessage;
  answer: FaqMessage;
}

export interface FaqFeedbackResponse {
  messageId: string;
  resolved: boolean;
  reasonCode: string | null;
}

export interface FaqKnowledgeChunk {
  id: string;
  title: string;
  section: string;
  body: string;
  metadata: Record<string, unknown>;
}

export interface RankedFaqChunk extends FaqKnowledgeChunk {
  position: number;
  score: number;
}
