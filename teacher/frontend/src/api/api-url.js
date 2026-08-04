export function normalizeApiBaseUrl(value) {
  return String(value || "").trim().replace(/\/+$/, "");
}

export function toApiUrl(path, apiBaseUrl = "") {
  if (/^https?:\/\//i.test(path)) return path;
  const normalizedPath = path.startsWith("/") ? path : `/${path}`;
  return `${normalizeApiBaseUrl(apiBaseUrl)}${normalizedPath}`;
}
