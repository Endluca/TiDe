import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { Client } from 'pg';

type QuizStatus = 'DRAFT' | 'PUBLISHED';

interface SourceQuestion {
  id: string;
  courseId?: string;
  type: 'single' | 'multiple' | 'boolean';
  question: string;
  questionZh?: string;
  options: string[];
  optionsZh?: string[];
  correct: number | number[];
  points?: number;
}

interface QuizSource {
  generatedFrom: string;
  sources: Record<string, string[]>;
  taskQuizBanks: Record<string, SourceQuestion[]>;
}

interface QuizDefinition {
  title: string;
  version: string;
  status: QuizStatus;
  passScore: number;
  expectedQuestionCount: number;
  mock?: boolean;
}

const quizDefinitions: Record<string, QuizDefinition> = {
  'profile-credentials': {
    title: 'G01 American TESOL',
    version: 'course-407-2026-07-v2',
    status: 'PUBLISHED',
    passScore: 80,
    expectedQuestionCount: 61,
  },
  'device-network': {
    title: 'Device and network course source bank',
    version: 'courses-526-596-2026-07',
    status: 'PUBLISHED',
    passScore: 80,
    expectedQuestionCount: 20,
  },
  'platform-policies': {
    title: 'G02 Platform Policies source bank',
    version: 'course-499-2026-07',
    status: 'PUBLISHED',
    passScore: 80,
    expectedQuestionCount: 5,
  },
  'lesson-preparation': {
    title: 'Lesson Preparation course source bank',
    version: 'courses-400-500-2026-07',
    status: 'PUBLISHED',
    passScore: 80,
    expectedQuestionCount: 10,
  },
  'me-culture': {
    title: 'G06 ME Culture and PARSNIP source bank',
    version: 'courses-520-398-2026-07',
    status: 'PUBLISHED',
    passScore: 80,
    expectedQuestionCount: 30,
  },
  'free-trial-training': {
    title: 'Free Trial candidate source bank',
    version: 'candidate-courses-324-510-2026-07',
    status: 'PUBLISHED',
    passScore: 80,
    expectedQuestionCount: 47,
  },
  'cocos-training': {
    title: 'G08 Cocos source bank',
    version: 'course-630-2026-07',
    status: 'PUBLISHED',
    passScore: 80,
    expectedQuestionCount: 5,
  },
};

const repoRoot = resolve(__dirname, '../..');
const sourcePath = resolve(repoRoot, 'backend/reference/task-quiz-banks.json');

const uuidFor = (value: string): string => {
  const hash = createHash('sha256').update(value).digest('hex').slice(0, 32);
  return `${hash.slice(0, 8)}-${hash.slice(8, 12)}-4${hash.slice(13, 16)}-8${hash.slice(17, 20)}-${hash.slice(20)}`;
};

const validateQuestion = (
  bankKey: string,
  question: SourceQuestion,
  questionKeys: Set<string>,
): void => {
  if (!question.id || questionKeys.has(question.id)) {
    throw new Error(
      `${bankKey}: duplicate or empty question id ${question.id}`,
    );
  }
  questionKeys.add(question.id);
  if (!['single', 'multiple', 'boolean'].includes(question.type)) {
    throw new Error(`${bankKey}/${question.id}: unsupported question type`);
  }
  if (!question.question.trim() || question.options.length < 2) {
    throw new Error(`${bankKey}/${question.id}: question or options missing`);
  }
  if (
    question.optionsZh &&
    question.optionsZh.length !== question.options.length
  ) {
    throw new Error(`${bankKey}/${question.id}: translated options mismatch`);
  }
  const answers = Array.isArray(question.correct)
    ? question.correct
    : [question.correct];
  if (
    answers.length === 0 ||
    answers.some(
      (answer) =>
        !Number.isInteger(answer) ||
        answer < 0 ||
        answer >= question.options.length,
    )
  ) {
    throw new Error(`${bankKey}/${question.id}: answer index is invalid`);
  }
  if (question.type === 'multiple' && !Array.isArray(question.correct)) {
    throw new Error(
      `${bankKey}/${question.id}: multiple answer must be an array`,
    );
  }
  if (question.type !== 'multiple' && Array.isArray(question.correct)) {
    throw new Error(
      `${bankKey}/${question.id}: single answer must be a number`,
    );
  }
};

const normalizedBank = (
  bankKey: string,
  questions: SourceQuestion[],
  definition: QuizDefinition,
) => {
  if (questions.length !== definition.expectedQuestionCount) {
    throw new Error(
      `${bankKey}: expected ${definition.expectedQuestionCount} questions, found ${questions.length}`,
    );
  }
  const questionKeys = new Set<string>();
  questions.forEach((question) =>
    validateQuestion(bankKey, question, questionKeys),
  );
  const publicQuestions = questions.map((question) => ({
    key: question.id,
    courseId: question.courseId ?? null,
    type: question.type.toUpperCase(),
    text: question.question,
    textZh: question.questionZh ?? question.question,
    options: question.options,
    optionsZh: question.optionsZh ?? question.options,
    ...(typeof question.points === 'number' ? { points: question.points } : {}),
  }));
  const answerKey = Object.fromEntries(
    questions.map((question) => [question.id, question.correct]),
  );
  const checksum = createHash('sha256')
    .update(JSON.stringify({ questions: publicQuestions, answerKey }))
    .digest('hex');
  return { publicQuestions, answerKey, checksum };
};

async function importBank(
  client: Client,
  source: QuizSource,
  bankKey: string,
  definition: QuizDefinition,
): Promise<number> {
  const sourceQuestions = source.taskQuizBanks[bankKey];
  if (!sourceQuestions) {
    throw new Error(`${bankKey}: source bank is missing`);
  }
  const { publicQuestions, answerKey, checksum } = normalizedBank(
    bankKey,
    sourceQuestions,
    definition,
  );
  const existing = await client.query<{
    status: string;
    contentChecksum: string;
  }>(
    `
      SELECT status, content_checksum AS "contentChecksum"
      FROM tide.task_quiz_banks
      WHERE bank_key = $1 AND question_set_version = $2
      FOR UPDATE
    `,
    [bankKey, definition.version],
  );
  if (
    existing.rows[0]?.status === 'PUBLISHED' &&
    existing.rows[0].contentChecksum !== checksum
  ) {
    throw new Error(
      `${bankKey}/${definition.version}: published bank changed; create a new version`,
    );
  }
  await client.query(
    `
      INSERT INTO tide.task_quiz_banks (
        quiz_bank_id, bank_key, question_set_version, title, status,
        pass_score, questions, answer_key, source_metadata,
        content_checksum, published_at
      ) VALUES (
        $1, $2, $3, $4, $5, $6, $7::jsonb, $8::jsonb, $9::jsonb, $10,
        CASE WHEN $5 = 'PUBLISHED' THEN now() ELSE NULL END
      )
      ON CONFLICT (bank_key, question_set_version) DO UPDATE SET
        title = EXCLUDED.title,
        status = EXCLUDED.status,
        pass_score = EXCLUDED.pass_score,
        questions = EXCLUDED.questions,
        answer_key = EXCLUDED.answer_key,
        source_metadata = EXCLUDED.source_metadata,
        content_checksum = EXCLUDED.content_checksum,
        published_at = CASE
          WHEN tide.task_quiz_banks.published_at IS NOT NULL
            THEN tide.task_quiz_banks.published_at
          ELSE EXCLUDED.published_at
        END,
        updated_at = now()
    `,
    [
      uuidFor(`quiz-bank:${bankKey}:${definition.version}`),
      bankKey,
      definition.version,
      definition.title,
      definition.status,
      definition.passScore,
      JSON.stringify(publicQuestions),
      JSON.stringify(answerKey),
      JSON.stringify({
        generatedFrom: source.generatedFrom,
        sourceFiles: source.sources[bankKey] ?? [],
        importedBy: 'backend/scripts/import-task-quiz-banks.ts',
        mock: Boolean(definition.mock),
      }),
      checksum,
    ],
  );
  return sourceQuestions.length;
}

async function main(): Promise<void> {
  const source = JSON.parse(readFileSync(sourcePath, 'utf8')) as QuizSource;
  const unknownBanks = Object.keys(source.taskQuizBanks).filter(
    (bankKey) => !quizDefinitions[bankKey],
  );
  const missingBanks = Object.keys(quizDefinitions).filter(
    (bankKey) => !source.taskQuizBanks[bankKey],
  );
  if (unknownBanks.length > 0 || missingBanks.length > 0) {
    throw new Error(
      `Quiz-bank manifest mismatch. Unknown: ${unknownBanks.join(', ') || '-'}; missing: ${missingBanks.join(', ') || '-'}`,
    );
  }
  const client = new Client(
    process.env.TASK_QUIZ_BANK_DATABASE_URL
      ? { connectionString: process.env.TASK_QUIZ_BANK_DATABASE_URL }
      : {
          host: process.env.TIDE_DB_HOST ?? '127.0.0.1',
          port: Number(process.env.TIDE_DB_PORT ?? 55432),
          user: process.env.TIDE_DB_USER,
          password: process.env.TIDE_DB_PASSWORD,
          database: process.env.TIDE_DB_NAME,
        },
  );
  await client.connect();
  try {
    await client.query('BEGIN');
    await client.query(
      `SELECT pg_advisory_xact_lock(hashtextextended($1, 0))`,
      ['task-quiz-bank-import'],
    );
    let questionCount = 0;
    for (const [bankKey, definition] of Object.entries(quizDefinitions)) {
      questionCount += await importBank(client, source, bankKey, definition);
    }
    await client.query('COMMIT');
    process.stdout.write(
      `Imported ${Object.keys(quizDefinitions).length} quiz banks and ${questionCount} questions into tide.task_quiz_banks.\n`,
    );
  } catch (error) {
    await client.query('ROLLBACK');
    throw error;
  } finally {
    await client.end();
  }
}

void main();
