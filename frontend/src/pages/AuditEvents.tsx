import { useCallback, useEffect, useRef, useState } from 'react'
import { Alert, Button, Card, Collapse, Flex, Input, Pagination, Select, Space, Tag, Typography } from 'antd'
import { ReloadOutlined, SearchOutlined } from '@ant-design/icons'
import { api, isRequestCancelled } from '../api'
import type { AuditEvent, TeacherOption } from '../types'
import { displayError, eventLabel } from '../domain'
import { JsonBlock, PageHeader, TimeText } from '../components/Common'
import { useI18n } from '../i18n'
import { useLatestRequest } from '../useLatestRequest'

const { Text } = Typography

const PAGE_SIZE = 20

export default function AuditEvents({ active = true }: { active?: boolean }) {
  const { locale, t } = useI18n()
  const eventRequest = useLatestRequest()
  const teacherRequest = useLatestRequest()
  const teacherSearchTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const [teacher, setTeacher] = useState<string>()
  const [keyword, setKeyword] = useState('')
  const [teacherOptions, setTeacherOptions] = useState<TeacherOption[]>([])
  const [events, setEvents] = useState<AuditEvent[]>([])
  const [expandedEventKeys, setExpandedEventKeys] = useState<string[]>([])
  const [page, setPage] = useState(1)
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(false)
  const [teacherLoading, setTeacherLoading] = useState(false)
  const [error, setError] = useState<string>()

  async function load(targetPage: number) {
    setLoading(true)
    setError(undefined)
    let cancelled = false
    try {
      const response = await eventRequest.run((options) =>
        api.events({
          page: targetPage,
          page_size: PAGE_SIZE,
          teacher_id: teacher,
          keyword: keyword.trim() || undefined,
        }, options),
      )
      setEvents(response.items)
      setTotal(response.total)
      setPage(response.page)
    } catch (reason) {
      if (isRequestCancelled(reason)) {
        cancelled = true
        return
      }
      setError(displayError(reason, locale))
    } finally {
      if (!cancelled) setLoading(false)
    }
  }

  const fetchTeacherOptions = useCallback(async (search: string) => {
    const keywordValue = search.trim()
    if (keywordValue.length === 1) {
      teacherRequest.cancel()
      setTeacherOptions([])
      setTeacherLoading(false)
      return
    }
    setTeacherLoading(true)
    let cancelled = false
    try {
      const options = await teacherRequest.run((requestOptions) =>
        api.teacherOptions({ keyword: keywordValue || undefined, limit: 30 }, requestOptions),
      )
      setTeacherOptions((current) => {
        const selected = current.find((item) => item.teacher_id === teacher)
        return selected && !options.some((item) => item.teacher_id === selected.teacher_id)
          ? [selected, ...options].slice(0, 30)
          : options
      })
    } catch (reason) {
      if (isRequestCancelled(reason)) {
        cancelled = true
        return
      }
      setError(displayError(reason, locale))
    } finally {
      if (!cancelled) setTeacherLoading(false)
    }
  }, [locale, teacher, teacherRequest])

  const scheduleTeacherSearch = useCallback((value: string) => {
    if (teacherSearchTimer.current) clearTimeout(teacherSearchTimer.current)
    teacherSearchTimer.current = setTimeout(() => {
      void fetchTeacherOptions(value)
    }, 250)
  }, [fetchTeacherOptions])

  useEffect(() => () => {
    if (teacherSearchTimer.current) clearTimeout(teacherSearchTimer.current)
  }, [])

  useEffect(() => {
    if (active) return
    if (teacherSearchTimer.current) {
      clearTimeout(teacherSearchTimer.current)
      teacherSearchTimer.current = null
    }
    teacherRequest.cancel()
    setTeacherLoading(false)
  }, [active, teacherRequest])

  if (!active) return null

  return <div className="page-shell">
    <PageHeader eyebrow={t('治理', 'Governance')} title={t('操作审计', 'Audit Log')} description={t('按教师和时间追溯任务状态、通知、运营介入与系统动作。', 'Trace task states, deliveries, operations actions, and system events by teacher and time.')} actions={<Space wrap><Select allowClear showSearch filterOption={false} loading={teacherLoading} onDropdownVisibleChange={(open) => { if (open && !teacherOptions.length) void fetchTeacherOptions('') }} onSearch={scheduleTeacherSearch} placeholder={t('输入至少 2 个字符筛选教师', 'Enter at least 2 characters')} value={teacher} onChange={setTeacher} style={{ width: 230 }} options={teacherOptions.map((item) => ({ value: item.teacher_id, label: `${item.teacher_id} · ${item.name}` }))} /><Input allowClear prefix={<SearchOutlined />} placeholder={t('搜索事件', 'Search events')} value={keyword} onChange={(event) => setKeyword(event.target.value)} onPressEnter={() => load(1)} /><Button icon={<ReloadOutlined />} loading={loading} onClick={() => load(1)}>{t('更新审计', 'Refresh audit')}</Button></Space>} />
    {error ? <Alert type="error" showIcon message={t('审计数据读取失败', 'Unable to load audit data')} description={error} /> : null}
    <Card loading={loading}>
      <Collapse
        ghost
        activeKey={expandedEventKeys}
        onChange={(keys) => setExpandedEventKeys((Array.isArray(keys) ? keys : [keys]).map(String))}
        items={events.map((event) => ({ key: event.event_id, label: <Flex gap={12} justify="space-between" wrap="wrap"><Space wrap><Tag color="blue">{eventLabel(event.event_type, locale)}</Tag>{event.runtime_event_code ? <Tag>{event.runtime_event_code}</Tag> : null}<Text>{event.teacher_id ?? t('系统', 'System')}</Text></Space><Text type="secondary"><TimeText value={event.occurred_at} /></Text></Flex>, children: <JsonBlock value={event} maxHeight={360} /> }))}
      />
      {!events.length ? <Text type="secondary">{t('暂无符合条件的事件', 'No matching events')}</Text> : null}
      {total > PAGE_SIZE ? <Flex justify="end" style={{ marginTop: 16 }}><Pagination current={page} pageSize={PAGE_SIZE} total={total} showSizeChanger={false} onChange={(targetPage) => load(targetPage)} /></Flex> : null}
    </Card>
  </div>
}
