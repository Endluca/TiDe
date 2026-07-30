import { apiRequest } from "./api-client";

export const sendAppEvent = (payload, anonymous = false) => apiRequest(
  anonymous ? "/api/v1/app-events/anonymous" : "/api/v1/app-events",
  {
  method: "POST",
    body: payload,
    auth: !anonymous,
  },
);

export const sendAppEvents = (
  payloads,
  anonymous = false,
  { keepalive = false } = {},
) => apiRequest(
  anonymous
    ? "/api/v1/app-events/anonymous/batch"
    : "/api/v1/app-events/batch",
  {
    method: "POST",
    body: { events: payloads },
    auth: !anonymous,
    keepalive,
  },
);
