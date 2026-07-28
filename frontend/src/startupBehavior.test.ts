import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

function source(path: string): string {
  return readFileSync(new URL(path, import.meta.url), 'utf8')
}

describe('首次进入加载边界', () => {
  it('只挂载当前会话已访问页面，不在登录后自动刷新经营数据', () => {
    const app = source('./App.tsx')

    expect(app).toContain('.filter((item) => visitedPages.has(item.key))')
    expect(app).not.toContain('setLoading(true)\n    refresh()')
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
    ]

    for (const path of pages) {
      expect(source(path)).not.toContain('useInitialLoad')
    }
    expect(source('./main.tsx')).not.toContain('React.StrictMode')
  })
})
