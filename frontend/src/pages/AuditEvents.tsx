import { useState } from 'react'
import { Alert, Button, Card, Collapse, Flex, Input, Pagination, Select, Space, Tag, Typography } from 'antd'
import { ReloadOutlined, SearchOutlined } from '@ant-design/icons'
import { api } from '../api'
import type { AuditEvent, TeacherOption } from '../types'
import { displayError, eventLabels } from '../domain'
import { JsonBlock, PageHeader, TimeText } from '../components/Common'

const { Text } = Typography

const PAGE_SIZE = 20

export default function AuditEvents() {
  const [teacher, setTeacher] = useState<string>()
  const [keyword, setKeyword] = useState('')
  const [teacherOptions, setTeacherOptions] = useState<TeacherOption[]>([])
  const [events, setEvents] = useState<AuditEvent[]>([])
  const [page, setPage] = useState(1)
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string>()

  async function load(targetPage: number) {
    setLoading(true)
    setError(undefined)
    try {
      const [teachers, response] = await Promise.all([
        teacherOptions.length ? Promise.resolve(teacherOptions) : api.teacherOptions(),
        api.events({
          page: targetPage,
          page_size: PAGE_SIZE,
          teacher_id: teacher,
          keyword: keyword.trim() || undefined,
        }),
      ])
      setTeacherOptions(teachers)
      setEvents(response.items)
      setTotal(response.total)
      setPage(response.page)
    } catch (reason) {
      setError(displayError(reason))
    } finally {
      setLoading(false)
    }
  }

  return <div className="page-shell">
    <PageHeader eyebrow="治理" title="操作审计" description="按教师和时间追溯任务状态、通知、运营介入与系统动作。" actions={<Space wrap><Select allowClear showSearch optionFilterProp="label" placeholder="筛选教师" value={teacher} onChange={setTeacher} style={{ width: 180 }} options={teacherOptions.map((item) => ({ value: item.teacher_id, label: `${item.teacher_id} · ${item.name}` }))} /><Input allowClear prefix={<SearchOutlined />} placeholder="搜索事件" value={keyword} onChange={(event) => setKeyword(event.target.value)} onPressEnter={() => load(1)} /><Button icon={<ReloadOutlined />} loading={loading} onClick={() => load(1)}>更新审计</Button></Space>} />
    {error ? <Alert type="error" showIcon message="审计数据读取失败" description={error} /> : null}
    <Card loading={loading}>
      <Collapse ghost items={events.map((event) => ({ key: event.event_id, label: <Flex gap={12} justify="space-between" wrap="wrap"><Space wrap><Tag color="blue">{eventLabels[event.event_type] ?? event.event_type}</Tag>{event.runtime_event_code ? <Tag>{event.runtime_event_code}</Tag> : null}<Text>{event.teacher_id ?? '系统'}</Text></Space><Text type="secondary"><TimeText value={event.occurred_at} /></Text></Flex>, children: <JsonBlock value={event} maxHeight={360} /> }))} />
      {!events.length ? <Text type="secondary">暂无符合条件的事件</Text> : null}
      {total > PAGE_SIZE ? <Flex justify="end" style={{ marginTop: 16 }}><Pagination current={page} pageSize={PAGE_SIZE} total={total} showSizeChanger={false} onChange={(targetPage) => load(targetPage)} /></Flex> : null}
    </Card>
  </div>
}
