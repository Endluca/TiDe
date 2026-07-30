import { Injectable } from '@nestjs/common';
import type { FaqKnowledgeChunk, RankedFaqChunk } from './faq.models';

const stopWords = new Set([
  'about',
  'and',
  'are',
  'can',
  'could',
  'does',
  'for',
  'from',
  'have',
  'how',
  'into',
  'is',
  'my',
  'please',
  'should',
  'that',
  'the',
  'this',
  'what',
  'when',
  'where',
  'which',
  'why',
  'with',
  'you',
  'your',
]);

const englishConcepts = new Map<string, string>([
  ['access', 'get'],
  ['download', 'get'],
  ['find', 'get'],
  ['grab', 'get'],
  ['locate', 'get'],
  ['obtain', 'get'],
  ['credential', 'certificate'],
  ['credentials', 'certificate'],
  ['evidence', 'certificate'],
  ['document', 'certificate'],
  ['documents', 'certificate'],
  ['certification', 'certificate'],
  ['proof', 'certificate'],
  ['send', 'submit'],
  ['post', 'submit'],
  ['upload', 'submit'],
  ['display', 'show'],
  ['visible', 'show'],
]);

@Injectable()
export class FaqRetriever {
  rank(
    question: string,
    chunks: FaqKnowledgeChunk[],
    limit = 10,
  ): RankedFaqChunk[] {
    const questionTokens = this.tokenize(question);
    if (questionTokens.length === 0) {
      return [];
    }
    const normalizedQuestion = this.normalize(question);
    const searchableChunks = chunks.map((chunk) => {
      const questionText =
        typeof chunk.metadata.question === 'string'
          ? chunk.metadata.question
          : '';
      const category =
        typeof chunk.metadata.category === 'string'
          ? chunk.metadata.category
          : '';
      const matchPhrases = this.stringList(chunk.metadata.matchPhrases);
      const searchAliases = this.stringList(chunk.metadata.searchAliases);
      const canonical = [questionText, ...matchPhrases, ...searchAliases].join(
        ' ',
      );
      const heading = `${chunk.title} ${chunk.section} ${category}`;
      const body = chunk.body;
      return {
        chunk,
        normalizedCanonical: this.normalize(canonical),
        canonicalTokens: new Set(this.tokenize(canonical)),
        headingTokens: new Set(this.tokenize(heading)),
        bodyTokens: new Set(this.tokenize(body)),
      };
    });
    const documentFrequency = new Map<string, number>();
    for (const token of questionTokens) {
      documentFrequency.set(
        token,
        searchableChunks.filter(
          (chunk) =>
            chunk.canonicalTokens.has(token) ||
            chunk.headingTokens.has(token) ||
            chunk.bodyTokens.has(token),
        ).length,
      );
    }
    const totalWeight = questionTokens.reduce(
      (total, token) =>
        total +
        this.inverseDocumentFrequency(
          chunks.length,
          documentFrequency.get(token) ?? 0,
        ),
      0,
    );

    return searchableChunks
      .map((searchable) => {
        let weightedHits = 0;
        let hitCount = 0;
        for (const token of questionTokens) {
          const rarity = this.inverseDocumentFrequency(
            chunks.length,
            documentFrequency.get(token) ?? 0,
          );
          if (searchable.canonicalTokens.has(token)) {
            weightedHits += rarity * 2.5;
            hitCount += 1;
          } else if (searchable.headingTokens.has(token)) {
            weightedHits += rarity * 1.5;
            hitCount += 1;
          } else if (searchable.bodyTokens.has(token)) {
            weightedHits += rarity;
            hitCount += 1;
          }
        }
        const exact =
          normalizedQuestion.length >= 4 &&
          searchable.normalizedCanonical.includes(normalizedQuestion);
        if (!exact && hitCount === 0) {
          return null;
        }
        return {
          ...searchable.chunk,
          position: 0,
          score: weightedHits / Math.max(1, totalWeight) + (exact ? 4 : 0),
        };
      })
      .filter((chunk): chunk is RankedFaqChunk => chunk !== null)
      .sort(
        (left, right) =>
          right.score - left.score || left.section.localeCompare(right.section),
      )
      .slice(0, limit)
      .map((chunk, index) => ({ ...chunk, position: index + 1 }));
  }

  normalizeQuestion(question: string): string {
    return question
      .toLowerCase()
      .replace(/[\w.+-]+@[\w.-]+\.[a-z]{2,}/gi, '[email]')
      .replace(/\b\d{8,}\b/g, '[id]')
      .replace(/\s+/g, ' ')
      .trim()
      .slice(0, 500);
  }

  private tokenize(value: string): string[] {
    const normalized = value.toLowerCase();
    const latin = normalized.match(/[a-z0-9][a-z0-9_-]{1,}/g) ?? [];
    return [
      ...new Set(
        latin
          .map((token) => this.normalizeEnglishToken(token))
          .filter((token) => token.length > 1 && !stopWords.has(token)),
      ),
    ];
  }

  private normalize(value: string): string {
    return value.toLowerCase().replace(/[\p{P}\p{S}\s]+/gu, '');
  }

  private normalizeEnglishToken(value: string): string {
    const concept = englishConcepts.get(value);
    if (concept) {
      return concept;
    }
    let token = value;
    if (token.length > 5 && token.endsWith('ing')) {
      token = token.slice(0, -3);
      if (
        token.length > 2 &&
        token.at(-1) === token.at(-2) &&
        !token.endsWith('ss')
      ) {
        token = token.slice(0, -1);
      }
    } else if (token.length > 4 && token.endsWith('ied')) {
      token = `${token.slice(0, -3)}y`;
    } else if (token.length > 4 && token.endsWith('ed')) {
      token = token.slice(0, -2);
    } else if (token.length > 4 && token.endsWith('ies')) {
      token = `${token.slice(0, -3)}y`;
    } else if (
      token.length > 4 &&
      token.endsWith('s') &&
      !token.endsWith('ss')
    ) {
      token = token.slice(0, -1);
    }
    return englishConcepts.get(token) ?? token;
  }

  private stringList(value: unknown): string[] {
    if (Array.isArray(value)) {
      return value.filter((item): item is string => typeof item === 'string');
    }
    return typeof value === 'string' ? [value] : [];
  }

  private inverseDocumentFrequency(total: number, matches: number): number {
    return Math.log((total + 1) / (matches + 1)) + 1;
  }
}
