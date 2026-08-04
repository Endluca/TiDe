import { parseAiJsonObject } from './ai-json-response';

describe('parseAiJsonObject', () => {
  it('parses a plain JSON object', () => {
    expect(parseAiJsonObject(' {"decision":"PASS"} ')).toEqual({
      decision: 'PASS',
    });
  });

  it('parses a JSON object wrapped in a Markdown fence', () => {
    expect(parseAiJsonObject('```json\n{"decision":"PASS"}\n```')).toEqual({
      decision: 'PASS',
    });
  });

  it('parses a single JSON object with a short prose wrapper', () => {
    expect(
      parseAiJsonObject('审核结果如下：\n{"decision":"RETRY"}\n请重新拍照。'),
    ).toEqual({ decision: 'RETRY' });
  });

  it('rejects invalid JSON and non-object JSON', () => {
    expect(parseAiJsonObject('not json')).toBeNull();
    expect(parseAiJsonObject('[{"decision":"PASS"}]')).toBeNull();
  });
});
