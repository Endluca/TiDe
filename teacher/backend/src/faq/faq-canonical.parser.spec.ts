import { parseCanonicalFaqMarkdown } from './faq-canonical.parser';

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
});
