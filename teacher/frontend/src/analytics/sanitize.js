import { PRODUCT_EVENT_PROPERTY_NAME_SET } from "./event-dictionary.js";

const SENSITIVE_PARTS = [
  "email",
  "password",
  "token",
  "answer",
  "questiontext",
  "prompt",
  "messagebody",
  "student",
  "image",
  "photo",
  "fileurl",
  "fileaddress",
  "downloadurl",
];

export function sanitizeAnalyticsProperties(input) {
  return Object.fromEntries(Object.entries(input || {}).flatMap(([key, value]) => {
    const normalized = key.replace(/[^a-z0-9]/gi, "").toLowerCase();
    if (
      !PRODUCT_EVENT_PROPERTY_NAME_SET.has(key)
      || SENSITIVE_PARTS.some((part) => normalized.includes(part))
    ) {
      return [];
    }
    if (
      value === null
      || ["string", "number", "boolean"].includes(typeof value)
    ) {
      return [[key, typeof value === "string" ? value.slice(0, 512) : value]];
    }
    return [];
  }));
}
