import type { ReactNode } from 'react'
import { Badge, Empty, Skeleton, Tag, Typography } from 'antd'
import dayjs from 'dayjs'
import type { CompletionMethod } from '../types'
import { methodLabel } from '../domain'
import { useI18n } from '../i18n'

const { Text, Title } = Typography

const methodColors: Record<CompletionMethod, string> = {
  QUIZ: 'blue',
  CHECKLIST: 'cyan',
  UPLOAD_REVIEW: 'purple',
  DEVICE_CHECK: 'geekblue',
  EXTERNAL_SYNC: 'gold',
  CONFIRMATION_FORM: 'volcano',
}

export function MethodTag({ method }: { method: CompletionMethod }) {
  const { locale } = useI18n()
  return <Tag color={methodColors[method]}>{methodLabel(method, locale)}</Tag>
}

export function PriorityTag({ priority }: { priority: string }) {
  const { t } = useI18n()
  const color = priority === 'P0' ? 'red' : priority === 'P1' ? 'orange' : priority === 'P2' ? 'blue' : 'default'
  const label = priority === 'P0'
    ? t('立即处理', 'Immediate')
    : priority === 'P1'
      ? t('优先处理', 'High priority')
      : priority === 'P2'
        ? t('常规跟进', 'Standard')
        : priority === 'P3'
          ? t('低优先级', 'Low priority')
          : priority
  return <Tag color={color}>{label}</Tag>
}

export function RuntimeTag({ status }: { status?: string | null }) {
  const { t } = useI18n()
  if (!status) return <Tag>{t('尚未开始', 'Not started')}</Tag>
  const map: Record<string, { color: string; label: string }> = {
    AVAILABLE: { color: 'blue', label: t('可开始', 'Available') },
    VIEWED: { color: 'cyan', label: t('已查看', 'Viewed') },
    STARTED: { color: 'processing', label: t('进行中', 'In progress') },
    SUBMITTED: { color: 'processing', label: t('已提交', 'Submitted') },
    VERIFYING: { color: 'gold', label: t('验证中', 'Verifying') },
    COMPLETED: { color: 'success', label: t('动作已验证完成', 'Verified complete') },
    RETRY_REQUIRED: { color: 'warning', label: t('需要重试', 'Retry required') },
    FAILED_FINAL: { color: 'error', label: t('最终未通过', 'Failed') },
  }
  const item = map[status] ?? { color: 'default', label: status }
  return <Tag color={item.color}>{item.label}</Tag>
}

export function CaseStatus({ status, externalStatus }: { status: string; externalStatus?: string }) {
  const { t } = useI18n()
  if (externalStatus === 'REQUESTED_PENDING_APPROVAL') {
    return <Badge status="warning" text={t('待审批 · 未执行', 'Approval pending · Not executed')} />
  }
  if (status === 'OPEN') return <Badge status="error" text={t('待运营处理', 'Pending operations action')} />
  if (status === 'ACTION_REQUESTED') return <Badge status="warning" text={t('动作请求已创建', 'Action request created')} />
  return <Badge status="default" text={status} />
}

export function PageHeader({
  eyebrow,
  title,
  description,
  actions,
}: {
  eyebrow?: string
  title: string
  description: string
  actions?: ReactNode
}) {
  return (
    <div className="page-heading">
      <div>
        {eyebrow ? <div className="eyebrow"><span />{eyebrow}</div> : null}
        <Title level={2}>{title}</Title>
        <Text type="secondary">{description}</Text>
      </div>
      {actions ? <div className="page-actions">{actions}</div> : null}
    </div>
  )
}

export function JsonBlock({ value, maxHeight = 420 }: { value: unknown; maxHeight?: number }) {
  const { t } = useI18n()
  return (
    <pre className="json-block" style={{ maxHeight }} tabIndex={0} aria-label={t('JSON 数据', 'JSON data')}>
      {JSON.stringify(value, null, 2)}
    </pre>
  )
}

export function TimeText({ value }: { value?: string | null }) {
  if (!value) return <Text type="secondary">—</Text>
  return <span title={value}>{dayjs(value).format('MM-DD HH:mm:ss')}</span>
}

export function PanelLoading() {
  return <Skeleton active paragraph={{ rows: 6 }} />
}

export function EmptyPanel({ description }: { description?: string }) {
  const { t } = useI18n()
  return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={description ?? t('暂无数据', 'No data')} />
}
