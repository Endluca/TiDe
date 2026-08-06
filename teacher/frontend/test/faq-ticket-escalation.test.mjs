import assert from "node:assert/strict";
import test from "node:test";

import {
  faqTicketContext,
  shouldOfferFaqTicket,
  teacherQuestionBefore,
} from "../src/faq-ticket-escalation.js";

test("offers a ticket for every non-answer except a clarification question", () => {
  for (const reasonCode of [
    "FAQ_NOT_FOUND",
    "FAQ_AI_UNAVAILABLE",
    "FAQ_AI_RESPONSE_INVALID",
  ]) {
    assert.equal(shouldOfferFaqTicket({
      role: "ASSISTANT",
      faqHit: false,
      reasonCode,
    }), true);
  }
  assert.equal(shouldOfferFaqTicket({
    role: "ASSISTANT",
    faqHit: false,
    reasonCode: "FAQ_CLARIFICATION_NEEDED",
  }), false);
  assert.equal(shouldOfferFaqTicket({
    role: "ASSISTANT",
    faqHit: true,
    reasonCode: "FAQ_MATCHED",
  }), false);
});

test("prefills the question immediately before the failed assistant answer", () => {
  const messages = [
    { role: "TEACHER", body: "First question" },
    { role: "ASSISTANT", body: "First answer" },
    { role: "TEACHER", body: "Question to escalate" },
    { role: "ASSISTANT", body: "No reliable answer" },
  ];
  assert.equal(teacherQuestionBefore(messages, 3), "Question to escalate");
});

test("adds traceable FAQ identifiers to the support-ticket context", () => {
  assert.deepEqual(
    faqTicketContext("conversation-1", {
      id: "message-1",
      reasonCode: "FAQ_NOT_FOUND",
    }),
    {
      faqEscalated: true,
      faqConversationId: "conversation-1",
      faqAssistantMessageId: "message-1",
      faqReasonCode: "FAQ_NOT_FOUND",
    },
  );
});
