import { lazy, Suspense, useCallback, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { Avatar, Button, Layout, Menu, Result, Spin, Tooltip, Typography } from 'antd'
import type { MenuProps } from 'antd'
import {
  AuditOutlined,
  BarChartOutlined,
  BellOutlined,
  CalendarOutlined,
  ControlOutlined,
  FileSearchOutlined,
  LogoutOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  ReloadOutlined,
  SettingOutlined,
  TeamOutlined,
  UnorderedListOutlined,
  UserOutlined,
} from '@ant-design/icons'
import dayjs from 'dayjs'
import { ApiError, api } from './api'
import { displayError } from './domain'
import type { AppNavigationContext, AppSnapshot, OperatorIdentity, OperatorRole } from './types'
import LoginPage from './pages/LoginPage'

const OperationalDashboard = lazy(() => import('./pages/OperationalDashboard'))
const TaskCenter = lazy(() => import('./pages/TaskCenter'))
const Teacher360 = lazy(() => import('./pages/Teacher360'))
const TemplateCenter = lazy(() => import('./pages/TemplateCenter'))
const AuditEvents = lazy(() => import('./pages/AuditEvents'))
const OutputCenter = lazy(() => import('./pages/OutputCenter'))
const ConfigCenter = lazy(() => import('./pages/ConfigCenter'))
const InterventionCenter = lazy(() => import('./pages/InterventionCenter'))
const LessonEvidenceCenter = lazy(() => import('./pages/LessonEvidenceCenter'))

const { Header, Sider, Content } = Layout
const { Text } = Typography

const emptySnapshot: AppSnapshot = {
  dashboard: null,
  teachers: [],
  templates: [],
  tasks: [],
  cases: [],
  queue: [],
  notifications: [],
  events: [],
}

interface NavigationItem {
  key: string
  icon: ReactNode
  label: string
  roles?: OperatorRole[]
}

const navigationSections: Array<{ key: string; label: string; items: NavigationItem[] }> = [
  {
    key: 'business',
    label: '经营与处置',
    items: [
      { key: 'ops', icon: <BarChartOutlined />, label: '经营总览' },
      { key: 'interventions', icon: <ControlOutlined />, label: '待办处置' },
      { key: 'tasks', icon: <UnorderedListOutlined />, label: '任务进展' },
      { key: 'outputs', icon: <BellOutlined />, label: '触达记录' },
    ],
  },
  {
    key: 'evidence',
    label: '教师与证据',
    items: [
      { key: 'teachers', icon: <TeamOutlined />, label: '教师档案' },
      { key: 'lessons', icon: <CalendarOutlined />, label: '课程证据' },
    ],
  },
  {
    key: 'strategy',
    label: '任务与规则',
    items: [
      { key: 'templates', icon: <FileSearchOutlined />, label: '任务规则', roles: ['CONFIG_PUBLISHER'] },
      { key: 'config', icon: <SettingOutlined />, label: '积分与门槛', roles: ['CONFIG_PUBLISHER'] },
    ],
  },
  {
    key: 'governance',
    label: '治理',
    items: [
      { key: 'audit', icon: <AuditOutlined />, label: '操作审计', roles: ['AUDITOR', 'SENIOR_REVIEWER'] },
    ],
  },
]

export default function App() {
  const [active, setActive] = useState('ops')
  const [collapsed, setCollapsed] = useState(false)
  const [operator, setOperator] = useState<OperatorIdentity | null>(null)
  const [authLoading, setAuthLoading] = useState(true)
  const [snapshot, setSnapshot] = useState<AppSnapshot>(emptySnapshot)
  const [loading, setLoading] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState('')
  const [navigationContexts, setNavigationContexts] = useState<Record<string, AppNavigationContext>>({})
  const [visitedPages, setVisitedPages] = useState<Set<string>>(() => new Set(['ops']))

  const refresh = useCallback(async () => {
    try {
      const canReadAudit = operator?.roles.some((role) => role === 'AUDITOR' || role === 'SENIOR_REVIEWER') ?? false
      const [dashboard, teachers, queue, events] = await Promise.all([
        api.dashboard(),
        api.teacherOptions(),
        api.queue(),
        canReadAudit ? api.events() : Promise.resolve([]),
      ])
      setSnapshot({ dashboard, teachers, templates: [], tasks: [], cases: [], queue, notifications: [], events })
      setError('')
    } catch (reason) {
      if (reason instanceof ApiError && reason.status === 401) setOperator(null)
      setError(displayError(reason))
      throw reason
    } finally {
      setLoading(false)
    }
  }, [operator])

  const manualRefresh = useCallback(async () => {
    setRefreshing(true)
    try {
      await refresh()
    } finally {
      setRefreshing(false)
    }
  }, [refresh])

  useEffect(() => {
    api.me()
      .then(setOperator)
      .catch(() => setOperator(null))
      .finally(() => setAuthLoading(false))
  }, [])

  const visibleSections = useMemo(
    () => navigationSections
      .map((section) => ({
        ...section,
        items: section.items.filter((item) => !item.roles || item.roles.some((role) => operator?.roles.includes(role))),
      }))
      .filter((section) => section.items.length > 0),
    [operator],
  )
  const visibleItems = useMemo(() => visibleSections.flatMap((section) => section.items), [visibleSections])
  const menuItems = useMemo<MenuProps['items']>(
    () => visibleSections.map((section) => ({
      type: 'group',
      key: section.key,
      label: section.label,
      children: section.items.map((item) => ({ key: item.key, icon: item.icon, label: item.label })),
    })),
    [visibleSections],
  )
  const activeItem = visibleItems.find((item) => item.key === active)
  const activeSection = visibleSections.find((section) => section.items.some((item) => item.key === active))

  const navigate = useCallback((pageKey: string, context: AppNavigationContext = {}) => {
    setNavigationContexts((current) => ({ ...current, [pageKey]: context }))
    setVisitedPages((current) => {
      if (current.has(pageKey)) return current
      const next = new Set(current)
      next.add(pageKey)
      return next
    })
    setActive(pageKey)
  }, [])

  const renderPage = useCallback((pageKey: string) => {
    const navigationContext = navigationContexts[pageKey] ?? {}
    if (pageKey === 'interventions') return <InterventionCenter initialContext={navigationContext} onNavigate={navigate} canDecideCase={operator?.roles.some((role) => role === 'CASE_OPERATOR' || role === 'SENIOR_REVIEWER') ?? false} />
    if (pageKey === 'tasks') return <TaskCenter snapshot={snapshot} />
    if (pageKey === 'outputs') return <OutputCenter snapshot={snapshot} />
    if (pageKey === 'teachers') return <Teacher360 initialTeacherId={navigationContext.teacherId} />
    if (pageKey === 'lessons') return <LessonEvidenceCenter initialContext={navigationContext} onNavigate={navigate} />
    if (pageKey === 'templates') return <TemplateCenter snapshot={snapshot} refresh={refresh} />
    if (pageKey === 'config') return <ConfigCenter />
    if (pageKey === 'audit') return <AuditEvents snapshot={snapshot} refresh={refresh} />
    return <OperationalDashboard snapshot={snapshot} refresh={manualRefresh} onNavigate={navigate} />
  }, [manualRefresh, navigate, navigationContexts, operator?.roles, refresh, snapshot])

  async function logout() {
    await api.logout().catch(() => undefined)
    setOperator(null)
    setSnapshot(emptySnapshot)
    setActive('ops')
    setNavigationContexts({})
    setVisitedPages(new Set(['ops']))
  }

  if (authLoading) {
    return <div className="app-loading"><Spin size="large" tip="正在进入运营工作台…"><div /></Spin></div>
  }
  if (!operator) return <LoginPage onAuthenticated={setOperator} />

  const updatedAt = snapshot.dashboard?.as_of
    ? dayjs(snapshot.dashboard.as_of).format('MM-DD HH:mm')
    : '等待首次更新'
  const operatorName = operator.display_name && !/^(test|tester|admin)$/i.test(operator.display_name.trim())
    ? operator.display_name
    : '运营用户'

  return (
    <Layout className="app-layout">
      <Sider
        className="app-sider"
        width={248}
        collapsedWidth={64}
        collapsible
        collapsed={collapsed}
        trigger={null}
        breakpoint="lg"
        onBreakpoint={setCollapsed}
      >
        <div className={collapsed ? 'brand brand-collapsed' : 'brand'}>
          <div className="brand-mark">T</div>
          {!collapsed ? <div><strong>新师成长运营台</strong><span>30 天达标引导</span></div> : null}
        </div>
        <Menu theme="dark" mode="inline" selectedKeys={[active]} items={menuItems} onClick={({ key }) => navigate(key)} />
        <div className="sider-foot">
          <ControlOutlined />
          {!collapsed ? <span>让每次介入都可解释、可跟进</span> : null}
        </div>
      </Sider>
      <Layout>
        <Header className="app-header">
          <Button
            type="text"
            className="collapse-button"
            icon={collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
            onClick={() => setCollapsed((value) => !value)}
            aria-label={collapsed ? '展开导航' : '收起导航'}
          />
          <div className="header-context">
            <Text type="secondary">{activeSection?.label ?? '经营与处置'}</Text>
            <Text strong>{activeItem?.label ?? '经营总览'}</Text>
          </div>
          <div className="header-actions">
            <div className="data-freshness"><span>数据更新</span><strong>{updatedAt}</strong></div>
            <Tooltip title="刷新经营数据">
              <Button type="text" icon={<ReloadOutlined spin={refreshing} />} onClick={() => manualRefresh().catch(() => undefined)} aria-label="刷新经营数据" />
            </Tooltip>
            <div className="operator-chip"><Avatar size={30} icon={<UserOutlined />} /><span>{operatorName}</span></div>
            <Tooltip title="退出登录"><Button type="text" icon={<LogoutOutlined />} onClick={logout} aria-label="退出登录" /></Tooltip>
          </div>
        </Header>
        <Content className="app-content">
          {loading ? <div className="app-loading"><Spin size="large" tip="正在汇总经营数据…"><div /></Spin></div> : error && !snapshot.dashboard ? (
            <Result
              status="warning"
              title="经营数据暂时不可用"
              subTitle="系统暂时无法完成本次数据更新，请稍后重试。"
              extra={<Button type="primary" onClick={() => { setLoading(true); refresh().catch(() => undefined) }}>重新加载</Button>}
            />
          ) : (
            <Suspense fallback={<div className="page-loading"><Spin tip="正在打开页面…"><div /></Spin></div>}>
              {visibleItems
                .filter((item) => visitedPages.has(item.key))
                .map((item) => (
                  <div key={item.key} style={{ display: active === item.key ? 'block' : 'none' }}>
                    {renderPage(item.key)}
                  </div>
                ))}
            </Suspense>
          )}
        </Content>
      </Layout>
    </Layout>
  )
}
