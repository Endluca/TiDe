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

const testpaperTaskSchema = z
  .object({
    ...courseTaskBase,
    type: z.literal('TESTPAPER'),
    testpaperId: numericId.optional(),
  })
  .strict();

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
  mappingVersion: z.number().int().positive(),
  integrationStatus: z.enum(['ACTIVE', 'PARTIAL', 'MAPPING_ONLY']),
  launchEnabled: z.boolean(),
  completionEnabled: z.boolean(),
  autoCompleteAssignment: z.boolean().default(true),
  noHeader: z.boolean().default(true),
  courses: z.array(courseSchema).min(1),
};

const taskMappingSchema = z.object(taskMappingFields).strict();

const configurationSchema = z
  .object({
    version: z.literal(9),
    tasks: z.record(
      z.string().regex(/^(?:G0[1-9]|P-REL-ATTENDANCE)$/u),
      taskMappingSchema,
    ),
  })
  .strict();

export type KuozhiCourseTask = z.infer<typeof courseTaskSchema>;
export type KuozhiCourse = z.infer<typeof courseSchema>;
type KuozhiConfiguredTaskMapping = z.infer<typeof taskMappingSchema>;
export type KuozhiCourseMapping = Omit<
  KuozhiConfiguredTaskMapping,
  'mappingVersion'
>;
export type KuozhiCourseConfiguration = z.infer<typeof configurationSchema>;

export async function loadKuozhiCourseConfiguration(
  path: string,
): Promise<KuozhiCourseConfiguration> {
  const raw = await readFile(path, 'utf8');
  return configurationSchema.parse(JSON.parse(raw) as unknown);
}
