export function toFaqDisplayMarkdown(value) {
  if (typeof value !== "string") return "";
  return value
    .replace(/[ \t]*\[Source\s+\d+]/gi, "")
    .replace(/[ \t]+([,.;:!?])/g, "$1")
    .replace(/[ \t]+\n/g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}
