import { createHash } from 'node:crypto';

export interface CanonicalFaqItem {
  id: string;
  category: string;
  question: string;
  matchPhrases: string[];
  sources: string[];
  approvedAnswer: string;
  body: string;
}

export interface CanonicalFaqDocument {
  title: string;
  sourceVersion: string;
  sourceUrl: string | null;
  owner: string;
  language: string;
  contentHash: string;
  items: CanonicalFaqItem[];
}

const field = (body: string, name: string): string =>
  body.match(new RegExp(`^${name}:\\s*(.+)$`, 'mi'))?.[1]?.trim() ?? '';

export function parseCanonicalFaqMarkdown(
  markdown: string,
): CanonicalFaqDocument {
  const frontmatterMatch = markdown.match(/^---\r?\n([\s\S]*?)\r?\n---\r?\n/);
  if (!frontmatterMatch) {
    throw new Error('Canonical FAQ 缺少 frontmatter');
  }
  const metadata = Object.fromEntries(
    frontmatterMatch[1]
      .split(/\r?\n/)
      .map((line) => line.match(/^([a-z_]+):\s*(.*)$/i))
      .filter((match): match is RegExpMatchArray => match !== null)
      .map((match) => [match[1], match[2].trim()]),
  );
  if (
    metadata.status !== 'active' ||
    metadata.authority !== 'faq' ||
    metadata.answerable !== 'true'
  ) {
    throw new Error('Canonical FAQ 必须是 active、faq、answerable');
  }

  const documentBody = markdown.slice(frontmatterMatch[0].length);
  const title =
    documentBody.match(/^#\s+(.+)$/m)?.[1]?.trim() ?? '51Talk Teacher FAQ';
  const items = documentBody
    .split(/^##\s+/m)
    .slice(1)
    .map((section) => {
      const [heading, ...lines] = section.split(/\r?\n/);
      const headingMatch = heading.match(/^(FAQ-[A-Z]+-\d+)\s*[·.-]\s*(.+)$/);
      if (!headingMatch) {
        throw new Error(`FAQ 标题格式无效：${heading}`);
      }
      const sectionBody = lines.join('\n').trim();
      const approvedBlock =
        sectionBody.match(/Approved answer:\s*\n([\s\S]+)$/i)?.[1] ?? '';
      const approvedAnswer = approvedBlock
        .split(/\r?\n/)
        .map((line) => line.replace(/^\s*[-*]\s*/, '').trim())
        .filter(Boolean)
        .join('\n');
      const category = field(sectionBody, 'Domain');
      const question = field(sectionBody, 'Scenario') || headingMatch[2].trim();
      const matchPhrases = field(sectionBody, 'Match phrases')
        .split('|')
        .map((value) => value.trim())
        .filter(Boolean);
      const sources = field(sectionBody, 'Source')
        .split('|')
        .map((value) => value.trim())
        .filter(Boolean);
      if (!category || !approvedAnswer || sources.length === 0) {
        throw new Error(`FAQ ${headingMatch[1]} 缺少分类、答案或来源`);
      }
      return {
        id: headingMatch[1],
        category,
        question,
        matchPhrases,
        sources,
        approvedAnswer,
        body: [
          `Question: ${question}`,
          `Domain: ${category}`,
          `Match phrases: ${matchPhrases.join(' | ')}`,
          'Approved answer:',
          approvedAnswer,
        ].join('\n'),
      };
    });

  if (new Set(items.map((item) => item.id)).size !== items.length) {
    throw new Error('Canonical FAQ 存在重复 ID');
  }

  return {
    title,
    sourceVersion: metadata.source_version ?? '',
    sourceUrl: metadata.source_url || null,
    owner: metadata.owner ?? '',
    language: (metadata.languages ?? 'en').split(',')[0].trim(),
    contentHash: createHash('sha256').update(markdown).digest('hex'),
    items,
  };
}
