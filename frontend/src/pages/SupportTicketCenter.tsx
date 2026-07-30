import { useCallback, useMemo, useState } from 'react'
import {
  Alert,
  App as AntdApp,
  Badge,
  Button,
  Card,
  Descriptions,
  Drawer,
  Empty,
  Flex,
  Input,
  Pagination,
  Select,
  Space,
  Spin,
  Statistic,
  Table,
  Tag,
  Typography,
} from 'antd'
import type { BadgeProps, TableColumnsType } from 'antd'
import {
  CustomerServiceOutlined,
  MessageOutlined,
  ReloadOutlined,
  SendOutlined,
} from '@ant-design/icons'
import dayjs from 'dayjs'
import { api, isRequestCancelled } from '../api'
import { PageHeader, TimeText } from '../components/Common'
import { displayError } from '../domain'
import {
  supportTicketCategoryLabel,
  supportTicketContextLabel,
  supportTicketLocationLabel,
  supportTicketWorkflowLabel,
} from '../supportTickets'
import type {
  SupportTicket,
  SupportTicketSummary,
  SupportTicketWorkflowState,
} from '../types'
import { useI18n } from '../i18n'
import { useLatestRequest } from '../useLatestRequest'

const { Text, Title } = Typography
const { TextArea } = Input

const CATEGORY_CODES = [
  'TASK_RULES',
  'LESSON_INFO',
  'SCORE_OR_REVIEW',
  'PRODUCT_FUNCTION',
  'ACCOUNT_LOGIN',
  'MEDIA_UPLOAD_CAMERA',
  'OTHER',
]

function workflowBadge(
  state: SupportTicketWorkflowState,
  locale: 'zh-CN' | 'en-US',
) {
  const badgeStatus: BadgeProps['status'] = state === 'WAITING_OPERATOR'
    ? 'error'
    : state === 'WAITING_TEACHER'
      ? 'processing'
      : 'default'
  return <Badge status={badgeStatus} text={supportTicketWorkflowLabel(state, locale)} />
}

function safeContextEntries(context?: Record<string, unknown>) {
  if (!context) return []
  return Object.entries(context)
    .filter(([, value]) => ['string', 'number', 'boolean'].includes(typeof value))
    .filter(([, value]) => String(value).trim())
}

export default function SupportTicketCenter({
  active = true,
  canReply,
}: {
  active?: boolean
  canReply: boolean
}) {
  const { locale, t } = useI18n()
  const { message } = AntdApp.useApp()
  const listRequest = useLatestRequest()
  const summaryRequest = useLatestRequest()
  const detailRequest = useLatestRequest()
  const [summary, setSummary] = useState<SupportTicketSummary | null>(null)
  const [items, setItems] = useState<SupportTicket[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [workflowState, setWorkflowState] = useState<SupportTicketWorkflowState | undefined>('WAITING_OPERATOR')
  const [category, setCategory] = useState<string | undefined>()
  const [keyword, setKeyword] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [selected, setSelected] = useState<SupportTicket | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [reply, setReply] = useState('')
  const [replying, setReplying] = useState(false)

  const loadPage = useCallback(async (nextPage = page, nextPageSize = pageSize) => {
    setLoading(true)
    setError('')
    let cancelled = false
    try {
      const pageResponse = await listRequest.run((options) =>
        api.supportTickets({
          workflow_state: workflowState,
          secondary_category: category,
          keyword: keyword.trim() || undefined,
          page: nextPage,
          page_size: nextPageSize,
        }, options),
      )
      setItems(pageResponse.items)
      setTotal(pageResponse.total)
      setPage(pageResponse.page)
      setPageSize(pageResponse.page_size)
    } catch (reason) {
      if (isRequestCancelled(reason)) {
        cancelled = true
        return
      }
      setError(displayError(reason, locale))
    } finally {
      if (!cancelled) setLoading(false)
    }
  }, [category, keyword, listRequest, locale, page, pageSize, workflowState])

  const loadSummary = useCallback(async () => {
    try {
      setSummary(await summaryRequest.run((options) => api.supportTicketSummary(options)))
    } catch (reason) {
      if (!isRequestCancelled(reason)) setError(displayError(reason, locale))
    }
  }, [locale, summaryRequest])

  const refreshTickets = useCallback(async () => {
    await Promise.all([loadSummary(), loadPage(1, pageSize)])
  }, [loadPage, loadSummary, pageSize])

  const openTicket = useCallback(async (ticket: SupportTicket) => {
    setSelected(ticket)
    setDetailLoading(true)
    setReply('')
    let cancelled = false
    try {
      setSelected(await detailRequest.run((options) => api.supportTicket(ticket.ticket_id, options)))
    } catch (reason) {
      if (isRequestCancelled(reason)) {
        cancelled = true
        return
      }
      message.error(displayError(reason, locale))
    } finally {
      if (!cancelled) setDetailLoading(false)
    }
  }, [detailRequest, locale, message])

  const submitReply = useCallback(async () => {
    if (!selected || !reply.trim()) return
    setReplying(true)
    try {
      const updated = await api.replySupportTicket(selected.ticket_id, {
        message_id: crypto.randomUUID(),
        expected_row_version: selected.row_version,
        content: reply.trim(),
      })
      setSelected(updated)
      setReply('')
      message.success(t('回复已写入共享工单', 'Reply saved to the shared ticket'))
      await Promise.all([loadPage(page, pageSize), loadSummary()])
    } catch (reason) {
      message.error(displayError(reason, locale))
      if (selected) {
        const ticketId = selected.ticket_id
        detailRequest
          .run((options) => api.supportTicket(ticketId, options))
          .then((updated) => setSelected((current) => current?.ticket_id === ticketId ? updated : current))
          .catch(() => undefined)
      }
    } finally {
      setReplying(false)
    }
  }, [detailRequest, loadPage, loadSummary, locale, message, page, pageSize, reply, selected, t])

  const columns = useMemo<TableColumnsType<SupportTicket>>(() => [
    {
      title: t('工单', 'Ticket'),
      key: 'ticket',
      width: 235,
      render: (_, ticket) => (
        <Space direction="vertical" size={2}>
          <Button type="link" className="table-link" onClick={() => openTicket(ticket)}>
            {ticket.ticket_id.slice(0, 8)}
          </Button>
          <Text type="secondary">{ticket.teacher_name} · {ticket.teacher_id}</Text>
        </Space>
      ),
    },
    {
      title: t('问题类型', 'Category'),
      key: 'category',
      width: 170,
      render: (_, ticket) => (
        <Space direction="vertical" size={2}>
          <Tag>{supportTicketCategoryLabel(ticket.secondary_category, locale)}</Tag>
          <Text type="secondary">{supportTicketLocationLabel(ticket.problem_location, locale)}</Text>
        </Space>
      ),
    },
    {
      title: t('最新消息', 'Latest message'),
      key: 'latest',
      render: (_, ticket) => (
        <Space direction="vertical" size={3}>
          <Text ellipsis={{ tooltip: ticket.latest_message?.content }}>
            {ticket.latest_message?.content || t('仅含图片或结构化信息', 'Image or structured information only')}
          </Text>
          <Text type="secondary">
            {ticket.latest_message?.sender === 'OPERATOR' ? t('运营', 'Operations') : t('教师', 'Teacher')}
            {' · '}
            {ticket.message_count} {t('条消息', 'messages')}
          </Text>
        </Space>
      ),
    },
    {
      title: t('当前状态', 'Status'),
      dataIndex: 'workflow_state',
      width: 150,
      render: (value: SupportTicketWorkflowState) => workflowBadge(value, locale),
    },
    {
      title: t('更新时间', 'Updated'),
      dataIndex: 'updated_at',
      width: 145,
      render: (value: string) => <TimeText value={value} />,
    },
  ], [locale, openTicket, t])

  if (!active) return null

  const contextEntries = safeContextEntries(selected?.problem_context)
  const messages = selected?.messages ?? []

  return (
    <div className="page-shell">
      <PageHeader
        eyebrow={t('教师沟通', 'Teacher Communication')}
        title={t('教师工单', 'Teacher Support Tickets')}
        description={t(
          '查看教师提交的问题，直接在共享工单中回复，并持续跟进到关闭。',
          'Review teacher questions, reply in the shared ticket, and follow each issue through closure.',
        )}
        actions={(
          <Button icon={<ReloadOutlined />} loading={loading} onClick={() => refreshTickets()}>
            {t('更新工单', 'Refresh tickets')}
          </Button>
        )}
      />

      {!summary && !loading ? (
        <Alert
          type="info"
          showIcon
          message={t('工单尚未更新', 'Tickets not refreshed')}
          description={t(
            '进入页面不会自动请求数据。点击“更新工单”读取共享工单中的最新数据。',
            'Opening this page does not fetch data. Select “Refresh tickets” to read the latest shared tickets.',
          )}
        />
      ) : null}
      {error ? <Alert type="warning" showIcon message={t('工单读取失败', 'Ticket refresh failed')} description={error} /> : null}

      <div className="executive-metrics-grid support-ticket-summary">
        <Card className="executive-metric executive-metric-danger">
          <Statistic title={t('待运营回复', 'Waiting for operations')} value={summary?.waiting_operator ?? '—'} prefix={<CustomerServiceOutlined />} />
        </Card>
        <Card className="executive-metric executive-metric-warning">
          <Statistic title={t('待教师回复', 'Waiting for teacher')} value={summary?.waiting_teacher ?? '—'} prefix={<MessageOutlined />} />
        </Card>
        <Card className="executive-metric">
          <Statistic title={t('已关闭', 'Closed')} value={summary?.closed ?? '—'} />
        </Card>
      </div>

      <Card className="filter-card">
        <Flex gap={10} wrap="wrap">
          <Select<SupportTicketWorkflowState>
            allowClear
            value={workflowState}
            placeholder={t('全部状态', 'All statuses')}
            style={{ width: 170 }}
            onChange={(value) => { setWorkflowState(value); setPage(1) }}
            options={[
              { value: 'WAITING_OPERATOR', label: t('待运营回复', 'Waiting for operations') },
              { value: 'WAITING_TEACHER', label: t('待教师回复', 'Waiting for teacher') },
              { value: 'CLOSED', label: t('已关闭', 'Closed') },
            ]}
          />
          <Select
            allowClear
            value={category}
            placeholder={t('全部问题类型', 'All categories')}
            style={{ width: 210 }}
            onChange={(value) => { setCategory(value); setPage(1) }}
            options={CATEGORY_CODES.map((code) => ({
              value: code,
              label: supportTicketCategoryLabel(code, locale),
            }))}
          />
          <Input.Search
            allowClear
            value={keyword}
            onChange={(event) => setKeyword(event.target.value)}
            onSearch={() => loadPage(1, pageSize)}
            placeholder={t('教师姓名 / Teacher ID / 工单 ID', 'Teacher name / ID / ticket ID')}
            style={{ width: 320 }}
          />
        </Flex>
      </Card>

      <Card className="action-table-card">
        <Table
          rowKey="ticket_id"
          loading={loading}
          columns={columns}
          dataSource={items}
          pagination={false}
          scroll={{ x: 980 }}
          locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={t('当前筛选下没有工单', 'No tickets match these filters')} /> }}
        />
        {total > pageSize ? (
          <Flex justify="flex-end" className="table-pagination">
            <Pagination
              current={page}
              pageSize={pageSize}
              total={total}
              showSizeChanger
              onChange={(nextPage, nextPageSize) => loadPage(nextPage, nextPageSize)}
            />
          </Flex>
        ) : null}
      </Card>

      <Drawer
        open={Boolean(selected)}
        onClose={() => {
          detailRequest.cancel()
          setDetailLoading(false)
          setSelected(null)
        }}
        width={760}
        title={t('工单详情', 'Ticket Details')}
      >
        <Spin spinning={detailLoading}>
          {selected ? (
            <Space direction="vertical" size={16} style={{ width: '100%' }}>
              <Flex justify="space-between" align="flex-start" gap={12} wrap="wrap">
                <div>
                  <Title level={4}>{selected.teacher_name}</Title>
                  <Text type="secondary">{selected.teacher_id} · {selected.ticket_id}</Text>
                </div>
                {workflowBadge(selected.workflow_state, locale)}
              </Flex>

              <Descriptions size="small" column={2} bordered>
                <Descriptions.Item label={t('问题类型', 'Category')}>
                  {supportTicketCategoryLabel(selected.secondary_category, locale)}
                </Descriptions.Item>
                <Descriptions.Item label={t('问题位置', 'Location')}>
                  {supportTicketLocationLabel(selected.problem_location, locale)}
                </Descriptions.Item>
                <Descriptions.Item label={t('创建时间', 'Created')}>
                  {dayjs(selected.created_at).format('YYYY-MM-DD HH:mm')}
                </Descriptions.Item>
                <Descriptions.Item label={t('最后更新', 'Last updated')}>
                  {dayjs(selected.updated_at).format('YYYY-MM-DD HH:mm')}
                </Descriptions.Item>
                {contextEntries.map(([key, value]) => (
                  <Descriptions.Item key={key} label={supportTicketContextLabel(key, locale)}>
                    {String(value)}
                  </Descriptions.Item>
                ))}
              </Descriptions>

              <Card size="small" title={t('沟通记录', 'Conversation')}>
                <div className="ticket-conversation">
                  {messages.map((item, index) => {
                    const operatorMessage = item.sender === 'OPERATOR'
                    return (
                      <div
                        className={`ticket-message ${operatorMessage ? 'ticket-message-operator' : 'ticket-message-teacher'}`}
                        key={item.message_id ?? `${item.created_at}-${index}`}
                      >
                        <Flex justify="space-between" gap={10}>
                          <Text strong>{operatorMessage ? t('运营', 'Operations') : t('教师', 'Teacher')}</Text>
                          <Text type="secondary">{item.created_at ? dayjs(item.created_at).format('MM-DD HH:mm') : '—'}</Text>
                        </Flex>
                        {item.content ? <div className="ticket-message-content">{item.content}</div> : null}
                        {item.images.map((image, imageIndex) => (
                          <div className="ticket-image-meta" key={image.file_id ?? imageIndex}>
                            <Text>{image.filename || t('图片附件', 'Image attachment')}</Text>
                            <Text type="secondary">
                              {image.deleted_at
                                ? t('图片已清理', 'Image deleted')
                                : image.preview_url
                                  ? t('可预览', 'Preview available')
                                  : t('暂不可预览', 'Preview unavailable')}
                            </Text>
                          </div>
                        ))}
                      </div>
                    )
                  })}
                  {!messages.length ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={t('暂无沟通记录', 'No messages')} /> : null}
                </div>
              </Card>

              {selected.workflow_state !== 'CLOSED' ? (
                <Card size="small" title={t('回复教师', 'Reply to Teacher')}>
                  {!canReply ? (
                    <Alert type="warning" showIcon message={t('当前账号没有回复权限', 'Your account cannot reply')} />
                  ) : (
                    <Space direction="vertical" size={10} style={{ width: '100%' }}>
                      <TextArea
                        value={reply}
                        onChange={(event) => setReply(event.target.value)}
                        rows={5}
                        maxLength={5000}
                        showCount
                        placeholder={t('写清处理结论、下一步和需要教师补充的信息。', 'State the resolution, next step, and any information the teacher should provide.')}
                      />
                      <Flex justify="space-between" align="center" gap={12}>
                        <Text type="secondary">{t('当前支持文字回复；图片回复暂未开放。', 'Text replies are currently supported; image replies are not yet available.')}</Text>
                        <Button
                          type="primary"
                          icon={<SendOutlined />}
                          loading={replying}
                          disabled={!reply.trim()}
                          onClick={submitReply}
                        >
                          {t('发送回复', 'Send reply')}
                        </Button>
                      </Flex>
                    </Space>
                  )}
                </Card>
              ) : (
                <Alert type="success" showIcon message={t('该工单已关闭', 'This ticket is closed')} description={selected.close_reason || undefined} />
              )}
            </Space>
          ) : null}
        </Spin>
      </Drawer>
    </div>
  )
}
