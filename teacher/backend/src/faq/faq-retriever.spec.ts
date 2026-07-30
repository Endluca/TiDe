import { FaqRetriever } from './faq-retriever';

const chunks = [
  {
    id: 'microphone',
    title: 'Device FAQ',
    section: 'Microphone check',
    body: 'Test your microphone in the classroom settings before class.',
    metadata: {},
  },
  {
    id: 'payment',
    title: 'Payment FAQ',
    section: 'Payment date',
    body: 'The approved payment answer.',
    metadata: {},
  },
];

const tesolChunks = [
  {
    id: 'tesol-upload',
    title: 'Teacher FAQ',
    section: 'FAQ-DOC-002',
    body: 'Question: How do I upload my TESOL certificate?\nApproved answer: Upload it in Account Settings.',
    metadata: {
      faqId: 'FAQ-DOC-002',
      question: 'How do I upload my TESOL certificate?',
      category: 'Documents',
    },
  },
  {
    id: 'tesol-failed',
    title: 'Teacher FAQ',
    section: 'FAQ-DOC-004',
    body: 'Question: What should I do if I fail the TESOL test?\nApproved answer: Retake the course and essay.',
    metadata: {
      faqId: 'FAQ-DOC-004',
      question: 'What should I do if I fail the TESOL test?',
      category: 'Documents',
    },
  },
  {
    id: 'tesol-get',
    title: 'Teacher FAQ',
    section: 'FAQ-DOC-006',
    body: 'Question: Where can I get my TESOL certificate?\nApproved answer: Follow the certificate request policy.',
    metadata: {
      faqId: 'FAQ-DOC-006',
      question: 'Where can I get my TESOL certificate?',
      category: 'Documents',
    },
  },
  {
    id: 'tesol-missing',
    title: 'Teacher FAQ',
    section: 'FAQ-DOC-007',
    body: "Question: Why can't I see my TESOL certificate in Mypage?\nApproved answer: Confirm both course parts are passed.",
    metadata: {
      faqId: 'FAQ-DOC-007',
      question: "Why can't I see my TESOL certificate in Mypage?",
      category: 'Documents',
    },
  },
];

describe('FaqRetriever', () => {
  const retriever = new FaqRetriever();

  it('returns only sufficiently matching approved chunks', () => {
    expect(
      retriever.rank('How should I test my microphone before class?', chunks),
    ).toEqual([expect.objectContaining({ id: 'microphone', position: 1 })]);
    expect(retriever.rank('What is the weather tomorrow?', chunks)).toEqual([]);
  });

  it('recalls the right FAQ for informal English and synonyms', () => {
    expect(retriever.rank('where can i find tesol', tesolChunks)[0]).toEqual(
      expect.objectContaining({ id: 'tesol-get', position: 1 }),
    );
    expect(
      retriever.rank('How can I access my TESOL document?', tesolChunks)[0],
    ).toEqual(expect.objectContaining({ id: 'tesol-get', position: 1 }));
    expect(
      retriever.rank(
        "My TESOL certificate isn't showing in MyPage",
        tesolChunks,
      )[0],
    ).toEqual(expect.objectContaining({ id: 'tesol-missing', position: 1 }));
    expect(
      retriever.rank(
        'Where is the proof that I finished the American teaching course?',
        tesolChunks,
      )[0],
    ).toEqual(expect.objectContaining({ id: 'tesol-missing', position: 1 }));
  });

  it('redacts common identifiers before aggregating a FAQ gap', () => {
    expect(
      retriever.normalizeQuestion(
        ' Contact Teacher.Name@51talk.com for account 123456789 ',
      ),
    ).toBe('contact [email] for account [id]');
  });
});
