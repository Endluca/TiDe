import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

function source(path: string): string {
  return readFileSync(new URL(path, import.meta.url), 'utf8')
}

describe('首次进入加载边界', () => {
  it('认证前不加载 AntD 运营壳，当前会话保留全部已访问页面控制器', () => {
    const app = source('./App.tsx')
    const workspace = source('./WorkspaceApp.tsx')

    expect(app).toContain("lazy(() => import('./LoginApplication'))")
    expect(app).toContain("lazy(() => import('./WorkspaceApplication'))")
    expect(app).not.toContain("from 'antd'")
    expect(workspace).toContain('new Set([\'ops\'])')
    expect(workspace).toContain('.filter((item) => visitedPages.has(item.key))')
    expect(workspace).toContain('default: memo(Page)')
    expect(workspace).not.toContain('MAX_MOUNTED_PAGES')
    expect(source('./pages/TaskCenter.tsx')).toContain('expandedRowKeys')
    expect(source('./pages/TaskCenter.tsx')).toContain('detailRequestFor.cancelAll()')
    expect(source('./pages/TaskCenter.tsx')).toContain('setExpandedRowKeys([])')
    expect(source('./pages/AuditEvents.tsx')).toContain('activeKey={expandedEventKeys}')
    expect(workspace).toContain('dashboardRequest.run')
    expect(workspace).toContain('已保留当前页面状态')
    expect(workspace).not.toContain('error && !snapshot.dashboard ?')
    expect(workspace).not.toContain('setLoading(true)\n    refresh()')
  })

  it('业务页面不在挂载时自动请求，开发入口不重复执行副作用', () => {
    const pages = [
      './pages/OperationalDashboard.tsx',
      './pages/InterventionCenter.tsx',
      './pages/TaskCenter.tsx',
      './pages/OutputCenter.tsx',
      './pages/Teacher360.tsx',
      './pages/LessonEvidenceCenter.tsx',
      './pages/TemplateCenter.tsx',
      './pages/ConfigCenter.tsx',
      './pages/SupportTicketCenter.tsx',
      './pages/AuditEvents.tsx',
    ]

    for (const path of pages) {
      const page = source(path)
      expect(page).not.toContain('useInitialLoad')
      expect(page).toContain('if (!active) return null')
    }
    expect(source('./main.tsx')).not.toContain('React.StrictMode')
  })

  it('工单翻页只读取列表，配置页复用工作台身份', () => {
    const tickets = source('./pages/SupportTicketCenter.tsx')
    const pageLoader = tickets.slice(
      tickets.indexOf('const loadPage'),
      tickets.indexOf('const loadSummary'),
    )
    const config = source('./pages/ConfigCenter.tsx')

    expect(pageLoader).toContain('api.supportTickets')
    expect(pageLoader).not.toContain('api.supportTicketSummary')
    expect(tickets).toContain('onChange={(nextPage, nextPageSize) => loadPage(nextPage, nextPageSize)}')
    expect(config).not.toContain("request<OperatorIdentity>('/api/auth/me')")
  })

  it('全量更新会先取消两个独立分页请求，避免旧分页覆盖新快照', () => {
    const outputs = source('./pages/OutputCenter.tsx')
    const fullLoader = outputs.slice(
      outputs.indexOf('const load = useCallback'),
      outputs.indexOf('async function loadAssignmentPage'),
    )

    expect(fullLoader).toContain('assignmentRequest.cancel()')
    expect(fullLoader).toContain('interventionRequest.cancel()')
    expect(fullLoader).not.toContain('outputRequest')
  })
})
