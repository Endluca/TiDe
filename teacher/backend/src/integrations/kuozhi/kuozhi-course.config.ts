import { readFile } from 'node:fs/promises';
import { z } from 'zod';

const numericId = z.string().trim().regex(/^\d+$/u);

const courseTaskBase = {
  courseTaskId: numericId,
  title: z.string().trim().min(1).optional(),
  required: z.boolean().default(true),
};

const videoTaskSchema = z
  .object({
    ...courseTaskBase,
    type: z.literal('VIDEO'),
    completionPercent: z.number().int().min(1).max(100).default(100),
  })
  .strict();

const passScoreSourceSchema = z.discriminatedUnion('kind', [
  z
    .object({
      kind: z.literal('QUIZ_BANK'),
      bankKey: z.string().trim().min(1),
      questionSetVersion: z.string().trim().min(1),
    })
    .strict(),
  z
    .object({
      kind: z.literal('FIXED'),
      percent: z.number().min(0).max(100),
    })
    .strict(),
]);

const testpaperTaskSchema = z
  .object({
    ...courseTaskBase,
    type: z.literal('TESTPAPER'),
    testpaperId: numericId.optional(),
    scoreMode: z.enum(['PERCENT', 'RAW_POINTS']),
    fullScore: z.number().positive().optional(),
    passScore: passScoreSourceSchema,
  })
  .strict()
  .superRefine((task, context) => {
    if (task.scoreMode === 'RAW_POINTS' && task.fullScore === undefined) {
      context.addIssue({
        code: 'custom',
        path: ['fullScore'],
        message: 'RAW_POINTS 必须配置 fullScore',
      });
    }
  });

const courseTaskSchema = z.discriminatedUnion('type', [
  videoTaskSchema,
  testpaperTaskSchema,
]);

const courseSchema = z
  .object({
    courseId: numericId,
    title: z.string().trim().min(1).optional(),
    tasks: z.array(courseTaskSchema).min(1),
  })
  .strict();

const taskMappingFields = {
  integrationStatus: z.enum(['ACTIVE', 'PARTIAL', 'MAPPING_ONLY']),
  launchEnabled: z.boolean(),
  completionEnabled: z.boolean(),
  noHeader: z.boolean().default(true),
  embedMode: z.enum(['IFRAME', 'NEW_WINDOW']).default('NEW_WINDOW'),
  courses: z.array(courseSchema).min(1),
};

const taskMappingSchema = z.object(taskMappingFields).strict();

const sampleProfileSchema = z
  .object({
    taskCode: z.string().regex(/^G0[1-9]$/u),
    ...taskMappingFields,
  })
  .strict();

const configurationSchema = z
  .object({
    version: z.literal(2),
    tasks: z.record(z.string().regex(/^G0[1-9]$/u), taskMappingSchema),
    sampleProfile: sampleProfileSchema,
  })
  .strict();

export type KuozhiCourseTask = z.infer<typeof courseTaskSchema>;
export type KuozhiCourse = z.infer<typeof courseSchema>;
export type KuozhiCourseMapping = z.infer<typeof taskMappingSchema>;
export type KuozhiCourseConfiguration = z.infer<typeof configurationSchema>;
export type KuozhiPassScoreSource = z.infer<typeof passScoreSourceSchema>;

export async function loadKuozhiCourseConfiguration(
  path: string,
): Promise<KuozhiCourseConfiguration> {
  const raw = await readFile(path, 'utf8');
  return configurationSchema.parse(JSON.parse(raw) as unknown);
}
