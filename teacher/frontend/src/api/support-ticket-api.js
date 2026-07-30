import { apiRequest } from "./api-client";

const toFormData = ({ description, secondaryCategory, problemLocation, context, rowVersion, images = [] }) => {
  const body = new FormData();
  body.set("description", description);
  if (secondaryCategory) body.set("secondaryCategory", secondaryCategory);
  if (problemLocation) body.set("problemLocation", problemLocation);
  if (context) body.set("context", JSON.stringify(context));
  if (rowVersion !== undefined) body.set("rowVersion", String(rowVersion));
  images.forEach((image) => body.append("images", image));
  return body;
};

export const listSupportTickets = (signal) =>
  apiRequest("/api/v1/support-tickets", { signal, cache: "no-store" });

export const createSupportTicket = (input) =>
  apiRequest("/api/v1/support-tickets", {
    method: "POST",
    body: toFormData(input),
    timeoutMs: 120_000,
  });

export const markSupportTicketRead = (ticketId) =>
  apiRequest(`/api/v1/support-tickets/${ticketId}/read`, {
    method: "POST",
    body: {},
  });

export const replySupportTicket = (ticketId, input) =>
  apiRequest(`/api/v1/support-tickets/${ticketId}/messages`, {
    method: "POST",
    body: toFormData(input),
    timeoutMs: 120_000,
  });

export const resolveSupportTicket = (ticketId, rowVersion) =>
  apiRequest(`/api/v1/support-tickets/${ticketId}/resolve`, {
    method: "POST",
    body: { rowVersion },
  });

export const getSupportTicketImage = (ticketId, fileId, signal) =>
  apiRequest(`/api/v1/support-tickets/${ticketId}/images/${fileId}`, {
    responseType: "blob",
    signal,
  });
