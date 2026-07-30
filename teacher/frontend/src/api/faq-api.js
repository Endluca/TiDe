import { apiRequest, newCommandKey } from "./api-client";

export const createFaqConversation = () => apiRequest("/api/v1/faq/conversations", { method: "POST", body: {} });
export const getFaqConversation = (conversationId, signal) => apiRequest(`/api/v1/faq/conversations/${conversationId}`, { signal });
export const newFaqMessageKey = () => newCommandKey("faq-message");
export const askFaqQuestion = (conversationId, message, idempotencyKey = newFaqMessageKey()) => apiRequest(`/api/v1/faq/conversations/${conversationId}/messages`, {
  method: "POST",
  headers: { "Idempotency-Key": idempotencyKey },
  body: { message },
});
export const saveFaqFeedback = (messageId, resolved, reasonCode) => apiRequest(`/api/v1/faq/messages/${messageId}/feedback`, {
  method: "POST",
  body: reasonCode ? { resolved, reasonCode } : { resolved },
});
