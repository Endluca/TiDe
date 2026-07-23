import type { ReactNode } from 'react'
import { Badge, Empty, Skeleton, Tag, Typography } from 'antd'
import dayjs from 'dayjs'
import type { CompletionMethod } from '../types'
import { methodLabels } from '../domain'

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
  return <Tag color={methodColors[method]}>{methodLabels[method] ?? method}</Tag>
}

export function PriorityTag({ priority }: { priority: string }) {
  const color = priority === 'P0' ? 'red' : priority === 'P1' ? 'orange' : priority === 'P2' ? 'blue' : 'default'
  const label = priority === 'P0'
    ? '立即处理'
    : priority === 'P1'
      ? '优先处理'
      : priority === 'P2'
        ? '常规跟进'
        : priority === 'P3'
          ? '低优先级'
          : priority
  return <Tag color={color}>{label}</Tag>
}

export function RuntimeTag({ status }: { status?: string | null }) {
  if (!status) return <Tag>尚未开始</Tag>
  const map: Record<string, { color: string; label: string }> = {
    AVAILABLE: { color: 'blue', label: '可开始' },
    VIEWED: { color: 'cyan', label: '已查看' },
    STARTED: { color: 'processing', label: '进行中' },
    SUBMITTED: { color: 'processing', label: '已提交' },
    VERIFYING: { color: 'gold', label: '验证中' },
    COMPLETED: { color: 'success', label: '动作已验证完成' },
    RETRY_REQUIRED: { color: 'warning', label: '需要重试' },
    FAILED_FINAL: { color: 'error', label: '最终未通过' },
  }
  const item = map[status] ?? { color: 'default', label: status }
  return <Tag color={item.color}>{item.label}</Tag>
}

export function CaseStatus({ status, externalStatus }: { status: string; externalStatus?: string }) {
  if (externalStatus === 'REQUESTED_PENDING_APPROVAL') {
    return <Badge status="warning" text="待审批 · 未执行" />
  }
  if (status === 'OPEN') return <Badge status="error" text="待运营处理" />
  if (status === 'ACTION_REQUESTED') return <Badge status="warning" text="动作请求已创建" />
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
  return (
    <pre className="json-block" style={{ maxHeight }} tabIndex={0} aria-label="JSON 数据">
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

export function EmptyPanel({ description = '暂无数据' }: { description?: string }) {
  return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={description} />
}
