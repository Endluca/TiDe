import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { Client } from 'pg';
import {
  parseCanonicalFaqMarkdown,
  type CanonicalFaqDocument,
} from '../src/faq/faq-canonical.parser';
import {
  FAQ_INTENT_MATCH_PROMPT_TEMPLATE,
  FAQ_INTENT_MATCH_PROMPT_VERSION,
  FAQ_INTENT_MATCH_RULE_VERSION,
  FAQ_PROMPT_RULE_VERSION,
  FAQ_PROMPT_TEMPLATE,
  FAQ_PROMPT_VERSION,
} from '../src/faq/faq-prompt';

const documentKey = 'teacher-faq-canonical-post-launch';
const expectedItemCount = 124;
const repoRoot = resolve(__dirname, '../..');
const canonicalPath = resolve(
  repoRoot,
  'backend/reference/teacher-faq-demo/knowledge/51Talk Teacher FAQ - Canonical.md',
);

const uuidFor = (value: string): string => {
  const hash = createHash('sha256').update(value).digest('hex').slice(0, 32);
  return `${hash.slice(0, 8)}-${hash.slice(8, 12)}-4${hash.slice(13, 16)}-8${hash.slice(17, 20)}-${hash.slice(20)}`;
};

const versionNumber = (value: string): number => {
  const digits = value.replace(/\D/g, '');
  if (!/^\d{8}$/.test(digits)) {
    throw new Error(`FAQ source_version 必须可转换为 YYYYMMDD：${value}`);
  }
  return Number(digits);
};

async function importDocument(
  client: Client,
  document: CanonicalFaqDocument,
): Promise<void> {
  const version = versionNumber(document.sourceVersion);
  const documentId = uuidFor(`${documentKey}:${version}`);
  const promptVersions = [
    {
      capability: 'FAQ_TEXT_ANSWER',
      version: FAQ_PROMPT_VERSION,
      ruleVersion: FAQ_PROMPT_RULE_VERSION,
      template: FAQ_PROMPT_TEMPLATE,
    },
    {
      capability: 'FAQ_INTENT_MATCH',
      version: FAQ_INTENT_MATCH_PROMPT_VERSION,
      ruleVersion: FAQ_INTENT_MATCH_RULE_VERSION,
      template: FAQ_INTENT_MATCH_PROMPT_TEMPLATE,
    },
  ].map((prompt) => ({
    ...prompt,
    id: uuidFor(
      `${prompt.capability}:${prompt.version}:${prompt.ruleVersion}`,
    ),
    hash: createHash('sha256').update(prompt.template).digest('hex'),
  }));

  await client.query('BEGIN');
  try {
    await client.query(
      `SELECT pg_advisory_xact_lock(hashtextextended($1, 0))`,
      [`faq-import:${documentKey}`],
    );
    const existing = await client.query<{
      contentHash: string;
    }>(
      `
        SELECT content_hash AS "contentHash"
        FROM tide.knowledge_documents
        WHERE document_key = $1 AND version = $2
      `,
      [documentKey, version],
    );
    if (
      existing.rows[0] &&
      existing.rows[0].contentHash !== document.contentHash
    ) {
      throw new Error(
        `FAQ ${document.sourceVersion} 已存在但内容哈希不同，请发布新版本`,
      );
    }

    await client.query(
      `
        INSERT INTO tide.knowledge_documents (
          id, document_key, version, title, language,
          authority_level, status, content_hash
        ) VALUES ($1, $2, $3, $4, $5, 'FAQ', 'DRAFT', $6)
        ON CONFLICT (document_key, version) DO NOTHING
      `,
      [
        documentId,
        documentKey,
        version,
        document.title,
        document.language,
        document.contentHash,
      ],
    );

    for (const [index, item] of document.items.entries()) {
      await client.query(
        `
          INSERT INTO tide.knowledge_chunks (
            id, document_id, chunk_key, position, body, retrieval_metadata
          ) VALUES ($1, $2, $3, $4, $5, $6)
          ON CONFLICT (document_id, chunk_key) DO NOTHING
        `,
        [
          uuidFor(`${documentId}:${item.id}`),
          documentId,
          item.id,
          index + 1,
          item.body,
          {
            answerable: true,
            faqId: item.id,
            category: item.category,
            question: item.question,
            matchPhrases: item.matchPhrases,
            sources: item.sources,
            sourceUrl: document.sourceUrl,
            sourceVersion: document.sourceVersion,
            owner: document.owner,
          },
        ],
      );
    }

    const chunkCount = await client.query<{ count: string }>(
      `SELECT count(*)::text AS count FROM tide.knowledge_chunks WHERE document_id = $1`,
      [documentId],
    );
    if (Number(chunkCount.rows[0].count) !== document.items.length) {
      throw new Error(
        `FAQ 分片数量异常：${chunkCount.rows[0].count}/${document.items.length}`,
      );
    }

    await client.query(
      `
        UPDATE tide.knowledge_documents
        SET status = 'RETIRED'
        WHERE document_key = $1 AND status = 'ACTIVE' AND id <> $2
      `,
      [documentKey, documentId],
    );
    await client.query(
      `
        UPDATE tide.knowledge_documents
        SET status = 'ACTIVE', activated_at = COALESCE(activated_at, now())
        WHERE id = $1
      `,
      [documentId],
    );

    for (const prompt of promptVersions) {
      await client.query(
        `
          INSERT INTO tide.ai_prompt_versions (
            id, capability, version, rule_version, status, prompt_hash
          ) VALUES ($1, $2, $3, $4, 'DRAFT', $5)
          ON CONFLICT (capability, version) DO UPDATE SET
            rule_version = EXCLUDED.rule_version,
            prompt_hash = EXCLUDED.prompt_hash
        `,
        [
          prompt.id,
          prompt.capability,
          prompt.version,
          prompt.ruleVersion,
          prompt.hash,
        ],
      );
      await client.query(
        `
          UPDATE tide.ai_prompt_versions
          SET status = 'RETIRED'
          WHERE capability = $1
            AND status = 'ACTIVE'
            AND id <> $2
        `,
        [prompt.capability, prompt.id],
      );
      await client.query(
        `
          UPDATE tide.ai_prompt_versions
          SET status = 'ACTIVE', activated_at = COALESCE(activated_at, now())
          WHERE id = $1
        `,
        [prompt.id],
      );
    }
    await client.query('COMMIT');
  } catch (error) {
    await client.query('ROLLBACK');
    throw error;
  }
}

async function main(): Promise<void> {
  const document = parseCanonicalFaqMarkdown(
    readFileSync(canonicalPath, 'utf8'),
  );
  if (document.items.length !== expectedItemCount) {
    throw new Error(
      `全量 FAQ 数量异常：${document.items.length}/${expectedItemCount}`,
    );
  }
  const summary = {
    documentKey,
    sourceVersion: document.sourceVersion,
    contentHash: document.contentHash,
    items: document.items.length,
    categories: new Set(document.items.map((item) => item.category)).size,
    promptVersions: {
      answer: FAQ_PROMPT_VERSION,
      intentMatch: FAQ_INTENT_MATCH_PROMPT_VERSION,
    },
  };
  if (process.argv.includes('--dry-run')) {
    process.stdout.write(
      `${JSON.stringify({ mode: 'dry-run', ...summary })}\n`,
    );
    return;
  }

  const databaseUrl = process.env.TIDE_DATABASE_URL;
  if (!databaseUrl) {
    throw new Error('TIDE_DATABASE_URL 不能为空');
  }
  const client = new Client({ connectionString: databaseUrl });
  await client.connect();
  try {
    await importDocument(client, document);
  } finally {
    await client.end();
  }
  process.stdout.write(`${JSON.stringify({ mode: 'imported', ...summary })}\n`);
}

void main().catch((error: unknown) => {
  const message = error instanceof Error ? error.message : 'FAQ 导入失败';
  process.stderr.write(`${message}\n`);
  process.exitCode = 1;
});
