import { describe, expect, it } from 'vitest'
import { retainVisitedPage } from './sessionPages'

describe('当前会话页面状态', () => {
  it('保留全部已访问页面，不因访问第四个页面淘汰旧状态', () => {
    let pages = new Set(['ops'])
    for (const page of ['teachers', 'lessons', 'tasks', 'audit']) {
      pages = retainVisitedPage(pages, page)
    }

    expect([...pages]).toEqual(['ops', 'teachers', 'lessons', 'tasks', 'audit'])
    expect(retainVisitedPage(pages, 'teachers')).toBe(pages)
  })
})
