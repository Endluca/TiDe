import { createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { z } from 'zod';

export const systemNotificationActionTypes = [
  'TASK_DETAIL',
  'MY_TIDE',
  'TASKS',
  'HELP',
  'ACCOUNT',
] as const;

export type SystemNotificationActionType =
  (typeof systemNotificationActionTypes)[number];

const taskStatuses = [
  'ASSIGNED',
  'VIEWED',
  'IN_PROGRESS',
  'SUBMITTED',
  'UNDER_REVIEW',
  'COMPLETED',
  'FAILED',
  'EXPIRED',
  'WAIVED',
  'CANCELLED',
] as const;

const englishText = (minimum: number, maximum: number) =>
  z
    .string()
    .trim()
    .min(minimum)
    .max(maximum)
    .refine(
      (value) => !/[\u3400-\u9fff]/u.test(value),
      '系统通知一期只允许英文内容',
    );

const taskCode = z
  .string()
  .trim()
  .min(1)
  .max(64)
  .refine(
    (value) => !/^G\d{2}$/u.test(value) || /^G0[1-9]$/u.test(value),
    '固定成长任务只允许当前 G01-G09 编码',
  );

const audienceFilterSchema = z
  .object({
    teacherIds: z.array(z.string().trim().min(1)).max(5_000).optional(),
    campBatchIds: z.array(z.string().trim().min(1)).max(500).optional(),
    campDay: z
      .object({
        min: z.number().int().min(0).max(365),
        max: z.number().int().min(0).max(365),
      })
      .strict()
      .refine((value) => value.min <= value.max, 'campDay 范围无效')
      .optional(),
    accountStatuses: z
      .array(z.enum(['ACTIVE', 'ENDED']))
      .max(2)
      .optional(),
    task: z
      .object({
        taskCodes: z.array(taskCode).min(1).max(100),
        statuses: z.array(z.enum(taskStatuses)).min(1).max(taskStatuses.length),
      })
      .strict()
      .optional(),
  })
  .strict()
  .refine(
    (value) =>
      Boolean(
        value.teacherIds?.length ||
        value.campBatchIds?.length ||
        value.campDay ||
        value.accountStatuses?.length ||
        value.task,
      ),
    '定向通知至少需要一个筛选条件',
  );

export const systemNotificationAudienceSchema = z.union([
  z.object({ all: z.literal(true) }).strict(),
  audienceFilterSchema,
]);

const actionSchema = z.discriminatedUnion('type', [
  z
    .object({
      type: z.literal('TASK_DETAIL'),
      taskCode,
    })
    .strict(),
  z.object({ type: z.literal('MY_TIDE') }).strict(),
  z.object({ type: z.literal('TASKS') }).strict(),
  z.object({ type: z.literal('HELP') }).strict(),
  z.object({ type: z.literal('ACCOUNT') }).strict(),
]);

const beijingDateTime = z
  .string()
  .refine(
    (value) =>
      /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?\+08:00$/u.test(value) &&
      !Number.isNaN(Date.parse(value)),
    '发布时间必须使用带 +08:00 的北京时间',
  );

export const systemNotificationPublicationSchema = z
  .object({
    configKey: z.string().regex(/^[a-z0-9][a-z0-9:_-]{2,127}$/u),
    typeCode: z
      .string()
      .regex(/^[A-Z][A-Z0-9_]{2,63}$/u)
      .refine(
        (value) =>
          [
            'SYSTEM_MAINTENANCE',
            'SERVICE_INCIDENT',
            'SERVICE_RESTORED',
            'FEATURE_UPDATE',
            'POLICY_UPDATE',
            'ACCOUNT_SECURITY',
            'MANUAL_ANNOUNCEMENT',
          ].includes(value),
        '配置发布不支持该系统通知类型',
      ),
    title: englishText(1, 160),
    body: englishText(1, 4_000),
    publishAt: beijingDateTime,
    expiresAt: beijingDateTime.optional(),
    cancelled: z.boolean().default(false),
    audience: systemNotificationAudienceSchema,
    action: actionSchema.optional(),
  })
  .strict()
  .refine(
    (value) =>
      !value.expiresAt ||
      Date.parse(value.expiresAt) > Date.parse(value.publishAt),
    '失效时间必须晚于发布时间',
  );

export type SystemNotificationPublicationConfig = z.infer<
  typeof systemNotificationPublicationSchema
>;

const configurationSchema = z
  .object({
    version: z.literal(1),
    publications: z.array(systemNotificationPublicationSchema).max(1_000),
  })
  .strict()
  .superRefine((value, context) => {
    const seen = new Set<string>();
    value.publications.forEach((publication, index) => {
      if (seen.has(publication.configKey)) {
        context.addIssue({
          code: 'custom',
          path: ['publications', index, 'configKey'],
          message: 'configKey 不能重复',
        });
      }
      seen.add(publication.configKey);
    });
  });

export type SystemNotificationConfiguration = z.infer<
  typeof configurationSchema
>;

export async function loadSystemNotificationConfiguration(
  path: string,
): Promise<SystemNotificationConfiguration> {
  const raw = await readFile(path, 'utf8');
  return configurationSchema.parse(JSON.parse(raw) as unknown);
}

export function publicationConfigHash(
  publication: SystemNotificationPublicationConfig,
): string {
  return createHash('sha256')
    .update(JSON.stringify(stableValue(publication)))
    .digest('hex');
}

export function publicationActionTarget(
  action: SystemNotificationPublicationConfig['action'],
): string | null {
  if (!action) return null;
  if (action.type === 'TASK_DETAIL') return action.taskCode;
  return {
    MY_TIDE: '/',
    TASKS: '/path',
    HELP: '/help',
    ACCOUNT: '/account',
  }[action.type];
}

function stableValue(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(stableValue);
  if (typeof value !== 'object' || value === null) return value;
  return Object.fromEntries(
    Object.entries(value)
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([key, child]) => [key, stableValue(child)]),
  );
}
