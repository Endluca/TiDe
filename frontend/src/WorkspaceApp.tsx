import { lazy, memo, Suspense, useCallback, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { Alert, Avatar, Button, Layout, Menu, Spin, Tooltip, Typography } from 'antd'
import type { MenuProps } from 'antd'
import {
  AuditOutlined,
  BarChartOutlined,
  BellOutlined,
  CalendarOutlined,
  ControlOutlined,
  CustomerServiceOutlined,
  FileSearchOutlined,
  GlobalOutlined,
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
import { ApiError, api, isRequestCancelled } from './api'
import { displayError } from './domain'
import { useI18n } from './i18n'
import { retainVisitedPage } from './sessionPages'
import type { AppNavigationContext, AppSnapshot, OperatorIdentity, OperatorRole } from './types'
import { useLatestRequest } from './useLatestRequest'

const OperationalDashboard = lazy(() => import('./pages/OperationalDashboard').then(({ default: Page }) => ({ default: memo(Page) })))
const TaskCenter = lazy(() => import('./pages/TaskCenter').then(({ default: Page }) => ({ default: memo(Page) })))
const Teacher360 = lazy(() => import('./pages/Teacher360').then(({ default: Page }) => ({ default: memo(Page) })))
const TemplateCenter = lazy(() => import('./pages/TemplateCenter').then(({ default: Page }) => ({ default: memo(Page) })))
const AuditEvents = lazy(() => import('./pages/AuditEvents').then(({ default: Page }) => ({ default: memo(Page) })))
const OutputCenter = lazy(() => import('./pages/OutputCenter').then(({ default: Page }) => ({ default: memo(Page) })))
const ConfigCenter = lazy(() => import('./pages/ConfigCenter').then(({ default: Page }) => ({ default: memo(Page) })))
const InterventionCenter = lazy(() => import('./pages/InterventionCenter').then(({ default: Page }) => ({ default: memo(Page) })))
const LessonEvidenceCenter = lazy(() => import('./pages/LessonEvidenceCenter').then(({ default: Page }) => ({ default: memo(Page) })))
const SupportTicketCenter = lazy(() => import('./pages/SupportTicketCenter').then(({ default: Page }) => ({ default: memo(Page) })))

const { Header, Sider, Content } = Layout
const { Text } = Typography

const emptySnapshot: AppSnapshot = {
  dashboard: null,
}
const emptyNavigationContext: AppNavigationContext = {}

interface NavigationItem {
  key: string
  icon: ReactNode
  label: string
  roles?: OperatorRole[]
}

export default function WorkspaceApp({
  initialOperator,
  onSignedOut,
}: {
  initialOperator: OperatorIdentity
  onSignedOut: () => void
}) {
  const { locale, isEnglish, t, toggleLocale } = useI18n()
  const [active, setActive] = useState('ops')
  const [visitedPages, setVisitedPages] = useState<Set<string>>(() => new Set(['ops']))
  const [collapsed, setCollapsed] = useState(false)
  const operator = initialOperator
  const [snapshot, setSnapshot] = useState<AppSnapshot>(emptySnapshot)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState('')
  const [navigationContexts, setNavigationContexts] = useState<Record<string, AppNavigationContext>>({})
  const dashboardRequest = useLatestRequest()

  const refresh = useCallback(async () => {
    try {
      const dashboard = await dashboardRequest.run((options) => api.dashboard(options))
      setSnapshot((current) => ({ ...current, dashboard }))
      setError('')
    } catch (reason) {
      if (isRequestCancelled(reason)) return
      if (reason instanceof ApiError && reason.status === 401) onSignedOut()
      setError(displayError(reason, locale))
      throw reason
    }
  }, [dashboardRequest, locale, onSignedOut])

  const manualRefresh = useCallback(async () => {
    setRefreshing(true)
    try {
      await refresh()
    } finally {
      setRefreshing(false)
    }
  }, [refresh])

  const navigationSections = useMemo<Array<{ key: string; label: string; items: NavigationItem[] }>>(
    () => [
      {
        key: 'business',
        label: t('经营与处置', 'Operations'),
        items: [
          { key: 'ops', icon: <BarChartOutlined />, label: t('经营总览', 'Overview') },
          { key: 'interventions', icon: <ControlOutlined />, label: t('待办处置', 'Action Queue') },
          { key: 'support-tickets', icon: <CustomerServiceOutlined />, label: t('教师工单', 'Teacher Tickets') },
          { key: 'tasks', icon: <UnorderedListOutlined />, label: t('任务进展', 'Task Progress') },
          { key: 'outputs', icon: <BellOutlined />, label: t('触达记录', 'Delivery Records') },
        ],
      },
      {
        key: 'evidence',
        label: t('教师与证据', 'Teachers & Evidence'),
        items: [
          { key: 'teachers', icon: <TeamOutlined />, label: t('教师档案', 'Teacher Profiles') },
          { key: 'lessons', icon: <CalendarOutlined />, label: t('课程证据', 'Lesson Evidence') },
        ],
      },
      {
        key: 'strategy',
        label: t('任务与规则', 'Tasks & Rules'),
        items: [
          { key: 'templates', icon: <FileSearchOutlined />, label: t('任务规则', 'Task Rules'), roles: ['CONFIG_PUBLISHER'] },
          { key: 'config', icon: <SettingOutlined />, label: t('积分与门槛', 'Scores & Thresholds'), roles: ['CONFIG_PUBLISHER'] },
        ],
      },
      {
        key: 'governance',
        label: t('治理', 'Governance'),
        items: [
          { key: 'audit', icon: <AuditOutlined />, label: t('操作审计', 'Audit Log'), roles: ['AUDITOR', 'SENIOR_REVIEWER'] },
        ],
      },
    ],
    [t],
  )

  const visibleSections = useMemo(
    () => navigationSections
      .map((section) => ({
        ...section,
        items: section.items.filter((item) => !item.roles || item.roles.some((role) => operator?.roles.includes(role))),
      }))
      .filter((section) => section.items.length > 0),
    [navigationSections, operator],
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
    setVisitedPages((current) => retainVisitedPage(current, pageKey))
    setActive(pageKey)
  }, [])

  const renderPage = useCallback((pageKey: string) => {
    const navigationContext = navigationContexts[pageKey] ?? emptyNavigationContext
    const pageActive = active === pageKey
    if (pageKey === 'interventions') return <InterventionCenter active={pageActive} initialContext={navigationContext} onNavigate={navigate} canDecideCase={operator.roles.some((role) => role === 'CASE_OPERATOR' || role === 'SENIOR_REVIEWER')} />
    if (pageKey === 'support-tickets') return <SupportTicketCenter active={pageActive} canReply={operator.roles.some((role) => role === 'CASE_OPERATOR' || role === 'SENIOR_REVIEWER')} />
    if (pageKey === 'tasks') return <TaskCenter active={pageActive} />
    if (pageKey === 'outputs') return <OutputCenter active={pageActive} />
    if (pageKey === 'teachers') return <Teacher360 active={pageActive} initialTeacherId={navigationContext.teacherId} />
    if (pageKey === 'lessons') return <LessonEvidenceCenter active={pageActive} initialContext={navigationContext} onNavigate={navigate} />
    if (pageKey === 'templates') return <TemplateCenter active={pageActive} />
    if (pageKey === 'config') return <ConfigCenter active={pageActive} operator={operator} />
    if (pageKey === 'audit') return <AuditEvents active={pageActive} />
    return <OperationalDashboard active={pageActive} snapshot={snapshot} refresh={manualRefresh} onNavigate={navigate} />
  }, [active, manualRefresh, navigate, navigationContexts, operator, snapshot])

  async function logout() {
    await api.logout().catch(() => undefined)
    setSnapshot(emptySnapshot)
    setActive('ops')
    setVisitedPages(new Set(['ops']))
    setNavigationContexts({})
    onSignedOut()
  }

  const updatedAt = snapshot.dashboard?.as_of
    ? dayjs(snapshot.dashboard.as_of).format('MM-DD HH:mm')
    : t('等待首次更新', 'Not updated yet')
  const operatorName = operator.display_name && !/^(test|tester|(?:ops[_-]?)?admin)$/i.test(operator.display_name.trim())
    ? operator.display_name
    : t('运营用户', 'Operations User')

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
          {!collapsed ? <div><strong>{t('新师成长运营台', 'New Teacher Growth')}</strong><span>{t('30 天达标引导', '30-Day Readiness')}</span></div> : null}
        </div>
        <Menu theme="dark" mode="inline" selectedKeys={[active]} items={menuItems} onClick={({ key }) => navigate(key)} />
        <div className="sider-foot">
          <ControlOutlined />
          {!collapsed ? <span>{t('让每次介入都可解释、可跟进', 'Every action stays explainable and traceable')}</span> : null}
        </div>
      </Sider>
      <Layout>
        <Header className="app-header">
          <Button
            type="text"
            className="collapse-button"
            icon={collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
            onClick={() => setCollapsed((value) => !value)}
            aria-label={collapsed ? t('展开导航', 'Expand navigation') : t('收起导航', 'Collapse navigation')}
          />
          <div className="header-context">
            <Text type="secondary">{activeSection?.label ?? t('经营与处置', 'Operations')}</Text>
            <Text strong>{activeItem?.label ?? t('经营总览', 'Overview')}</Text>
          </div>
          <div className="header-actions">
            <div className="data-freshness"><span>{t('数据更新', 'Data updated')}</span><strong>{updatedAt}</strong></div>
            <Tooltip title={t('刷新经营数据', 'Refresh overview data')}>
              <Button type="text" loading={refreshing} icon={<ReloadOutlined />} onClick={() => manualRefresh().catch(() => undefined)} aria-label={t('刷新经营数据', 'Refresh overview data')} />
            </Tooltip>
            <Tooltip title={t('切换为英文', 'Switch to Chinese')}>
              <Button
                type="text"
                className="language-toggle"
                icon={<GlobalOutlined />}
                onClick={toggleLocale}
                aria-label={t('切换为英文', 'Switch to Chinese')}
              >
                {isEnglish ? '中文' : 'EN'}
              </Button>
            </Tooltip>
            <div className="operator-chip"><Avatar size={30} icon={<UserOutlined />} /><span>{operatorName}</span></div>
            <Tooltip title={t('退出登录', 'Sign out')}><Button type="text" icon={<LogoutOutlined />} onClick={logout} aria-label={t('退出登录', 'Sign out')} /></Tooltip>
          </div>
        </Header>
        <Content className="app-content">
          {error ? (
            <Alert
              type="warning"
              showIcon
              message={t('经营数据本次更新失败，已保留当前页面状态', 'This refresh failed; the current page state was preserved')}
              description={error}
              action={<Button size="small" loading={refreshing} onClick={() => manualRefresh().catch(() => undefined)}>{t('重试', 'Try again')}</Button>}
            />
          ) : null}
          <Suspense fallback={<div className="page-loading"><Spin tip={t('正在打开页面…', 'Opening page…')}><div /></Spin></div>}>
            {visibleItems
              .filter((item) => visitedPages.has(item.key))
              .map((item) => (
                <div key={item.key} style={{ display: active === item.key ? 'block' : 'none' }}>
                  {renderPage(item.key)}
                </div>
              ))}
          </Suspense>
        </Content>
      </Layout>
    </Layout>
  )
}
