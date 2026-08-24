import {
  TaskDocumentContentService,
  TaskDocumentContentUnavailableError,
} from './task-document-content.service';

const config = {
  documentCode: 'overseas-nt-policies',
  contentVersion: '2026-07-24-overseas-nt-policies-v1',
  contentHash:
    '6875233667c6f3d90602a07c84849dbb88f41685929779a7b0dc79ccf859979c',
  readingCompletion: 'SCROLL_TO_END',
};

const lessonMemoConfig = {
  documentCode: 'lesson-memo-rules',
  contentVersion: '2026-07-24-lesson-memo-rules-v1',
  contentHash:
    '43dde8551988fa167103510da304feac63b853aa03d6c75747086932bf111b51',
  readingCompletion: 'SCROLL_TO_END',
};

describe('TaskDocumentContentService', () => {
  it('loads the execution-selected bilingual document and verified asset', () => {
    const service = new TaskDocumentContentService();

    const content = service.getContent(config);
    expect(content).toMatchObject({
      contentVersion: config.contentVersion,
      contentHash: config.contentHash,
      completionMode: 'SCROLL_TO_END',
      translationHashes: {
        zh: '46f526933d681b18118582364dcf203b2ba2c93d86d4f51ab468725d0a33a543',
      },
    });
    expect(content.title).toBe('Overseas NT Policies');
    expect(content.markdown.en).toContain('VSAC');
    expect(content.markdown.zh).toMatch(/[\u4e00-\u9fff]/u);

    const asset = service.getAsset(config, 'updated-unlocking-process');
    expect(asset.contentType).toBe('image/jpeg');
    expect(asset.sha256).toBe(
      '960dfdde25ba3b4b7362694714cd498f069482bfa4b09a97df8f3cc06a829fc9',
    );
    expect(asset.bytes.length).toBeGreaterThan(800_000);
  });

  it('fails closed when the execution requests an unknown content version', () => {
    const service = new TaskDocumentContentService();

    expect(() =>
      service.getContent({ ...config, contentVersion: 'future-version' }),
    ).toThrow(TaskDocumentContentUnavailableError);
  });

  it('serves the verified Lesson Memo section from the published policy asset', () => {
    const service = new TaskDocumentContentService();

    const content = service.getContent(lessonMemoConfig);

    expect(content).toMatchObject({
      documentCode: 'lesson-memo-rules',
      title: 'Lesson Memo Rules',
      contentVersion: lessonMemoConfig.contentVersion,
      contentHash: lessonMemoConfig.contentHash,
      completionMode: 'SCROLL_TO_END',
      translationHashes: {
        zh: 'ea513dac3e4c483211f61a9229602530acf9ddddcacfffe803a875f39edd58bf',
      },
      images: [],
    });
    expect(content.markdown.en).toMatch(/^# \*\*Lesson Memo \(LM\):\*\*/u);
    expect(content.markdown.en).toContain('Timing & Deadlines');
    expect(content.markdown.en).not.toContain('FEES');
    expect(content.markdown.zh).toContain('时间与截止期限');
  });

  it('does not expose unrelated policy assets to the Lesson Memo document', () => {
    const service = new TaskDocumentContentService();

    expect(() =>
      service.getAsset(lessonMemoConfig, 'updated-unlocking-process'),
    ).toThrow(TaskDocumentContentUnavailableError);
  });
});
