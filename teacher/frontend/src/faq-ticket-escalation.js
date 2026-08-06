export function shouldOfferFaqTicket(message) {
  return message?.role === "ASSISTANT"
    && message.faqHit === false
    && message.reasonCode !== "FAQ_CLARIFICATION_NEEDED";
}

export function teacherQuestionBefore(messages, messageIndex) {
  for (let index = messageIndex - 1; index >= 0; index -= 1) {
    if (messages[index]?.role === "TEACHER") return messages[index].body || "";
  }
  return "";
}

export function faqTicketContext(conversationId, message) {
  return {
    faqEscalated: true,
    faqConversationId: conversationId || null,
    faqAssistantMessageId: message?.id || null,
    faqReasonCode: message?.reasonCode || null,
  };
}
