export function safePolicyDocumentUrl(value) {
  if (typeof value !== "string" || !value.trim()) return null;
  try {
    const parsed = new URL(value);
    return parsed.protocol === "https:" && parsed.hostname
      ? parsed.href
      : null;
  } catch {
    return null;
  }
}

export function formatPolicyDocumentVersion(sourceUpdatedAt, contentVersion, language = "en") {
  const date = new Date(sourceUpdatedAt);
  if (sourceUpdatedAt && Number.isFinite(date.getTime())) {
    return new Intl.DateTimeFormat(language === "zh" ? "zh-CN" : "en-US", {
      year: "numeric",
      month: language === "zh" ? "numeric" : "short",
      day: "numeric",
      timeZone: "UTC",
    }).format(date);
  }
  return contentVersion || "—";
}
