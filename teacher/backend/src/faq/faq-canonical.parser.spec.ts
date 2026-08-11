import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { parseCanonicalFaqMarkdown } from './faq-canonical.parser';

const canonicalFaqPath = resolve(
  __dirname,
  '../../content/faq/51Talk Teacher FAQ - Canonical.md',
);

describe('parseCanonicalFaqMarkdown', () => {
  it('parses a self-contained canonical FAQ contract fixture', () => {
    const markdown = `---
status: active
authority: faq
answerable: true
source_version: 2026-07-21
source_url: https://alidocs.dingtalk.com/example
owner: Teacher Operations
languages: en
---
# 51Talk Teacher FAQ

## FAQ-ACC-001 · Update account details
Domain: Account, Portal & Personal Information
Scenario: How do I update my account details?
Match phrases: update account | change profile
Source: Teacher policy
Approved answer:
- Open the teacher portal and review your profile settings.

## FAQ-CLS-001 · Prepare for class
Domain: Class Preparation
Scenario: What should I check before class?
Match phrases: before class | lesson preparation
Source: Class readiness guide
Approved answer:
- Check your camera view and prepare the lesson slides.
`;
    const document = parseCanonicalFaqMarkdown(markdown);

    expect(document.items).toHaveLength(2);
    expect(document.sourceVersion).toBe('2026-07-21');
    expect(document.sourceUrl).toMatch(/^https:\/\/alidocs\.dingtalk\.com\//);
    expect(document.items[0]).toMatchObject({
      id: 'FAQ-ACC-001',
      category: 'Account, Portal & Personal Information',
    });
    expect(document.items.every((item) => item.approvedAnswer.length > 0)).toBe(
      true,
    );
    expect(document.contentHash).toMatch(/^[0-9a-f]{64}$/);
  });

  it('pins the reviewed 124-item canonical FAQ source', () => {
    const document = parseCanonicalFaqMarkdown(
      readFileSync(canonicalFaqPath, 'utf8'),
    );

    expect(document.sourceVersion).toBe('2026-07-21');
    expect(document.items).toHaveLength(124);
    expect(new Set(document.items.map((item) => item.id)).size).toBe(124);
    expect(document.contentHash).toBe(
      '1a3784f7d3bbc7a12e7b91d91da74c9522d969bd6cc16c0ab73683d16e080647',
    );
  });
});
