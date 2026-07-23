import { useMemo, useState } from 'react'
import { Button, Card, Collapse, Flex, Input, Select, Space, Tag, Typography } from 'antd'
import { ReloadOutlined, SearchOutlined } from '@ant-design/icons'
import type { AppSnapshot } from '../types'
import { eventLabels } from '../domain'
import { JsonBlock, PageHeader, TimeText } from '../components/Common'

const { Text } = Typography

export default function AuditEvents({ snapshot, refresh }: { snapshot: AppSnapshot; refresh: () => Promise<void> }) {
  const [teacher, setTeacher] = useState<string>()
  const [keyword, setKeyword] = useState('')
  const events = useMemo(() => snapshot.events.filter((item) => (!teacher || item.teacher_id === teacher) && JSON.stringify(item).toLowerCase().includes(keyword.toLowerCase())), [snapshot.events, teacher, keyword])
  return <div className="page-shell">
    <PageHeader eyebrow="治理" title="操作审计" description="按教师和时间追溯任务状态、通知、运营介入与系统动作。" actions={<Space wrap><Select allowClear placeholder="筛选教师" value={teacher} onChange={setTeacher} style={{ width: 160 }} options={snapshot.teachers.map((item) => ({ value: item.teacher_id, label: item.teacher_id }))} /><Input allowClear prefix={<SearchOutlined />} placeholder="搜索事件" value={keyword} onChange={(event) => setKeyword(event.target.value)} /><Button icon={<ReloadOutlined />} onClick={() => refresh()}>更新审计</Button></Space>} />
    <Card>
      <Collapse ghost items={events.map((event) => ({ key: event.event_id, label: <Flex gap={12} justify="space-between" wrap="wrap"><Space wrap><Tag color="blue">{eventLabels[event.event_type] ?? event.event_type}</Tag>{event.runtime_event_code ? <Tag>{event.runtime_event_code}</Tag> : null}<Text>{event.teacher_id ?? '系统'}</Text></Space><Text type="secondary"><TimeText value={event.occurred_at} /></Text></Flex>, children: <JsonBlock value={event} maxHeight={360} /> }))} />
      {!events.length ? <Text type="secondary">暂无符合条件的事件</Text> : null}
    </Card>
  </div>
}
