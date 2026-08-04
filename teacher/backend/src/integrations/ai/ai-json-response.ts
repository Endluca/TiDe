/**
 * Parses a JSON object returned by a model while tolerating the two wrappers
 * commonly produced despite an explicit JSON-only instruction: a Markdown
 * code fence or a short prose prefix/suffix. The caller must still validate
 * the parsed value with its domain schema before using it.
 */
export function parseAiJsonObject(
  content: string,
): Record<string, unknown> | null {
  const normalized = content.replace(/^\uFEFF/, '').trim();
  if (!normalized) return null;

  const fenced = normalized.match(/^```(?:json)?\s*([\s\S]*?)\s*```$/i);
  const candidates = [normalized];
  if (fenced?.[1]) candidates.unshift(fenced[1].trim());

  const firstBrace = normalized.indexOf('{');
  const lastBrace = normalized.lastIndexOf('}');
  const wrapper =
    firstBrace >= 0 && lastBrace > firstBrace
      ? normalized.slice(0, firstBrace) + normalized.slice(lastBrace + 1)
      : '';
  if (
    firstBrace >= 0 &&
    lastBrace > firstBrace &&
    !['[', ']', '{', '}'].some((character) => wrapper.includes(character))
  ) {
    candidates.push(normalized.slice(firstBrace, lastBrace + 1));
  }

  for (const candidate of new Set(candidates)) {
    try {
      const parsed = JSON.parse(candidate) as unknown;
      if (
        parsed !== null &&
        typeof parsed === 'object' &&
        !Array.isArray(parsed)
      ) {
        return parsed as Record<string, unknown>;
      }
    } catch {
      // Try the next narrowly-scoped wrapper candidate.
    }
  }
  return null;
}
