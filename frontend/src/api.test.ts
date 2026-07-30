import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  api,
  createLatestRequestController,
  isRequestCancelled,
  request,
  RequestTimeoutError,
} from './api'

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

function jsonResponse(body: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: status >= 400 ? 'Request failed' : 'OK',
    json: async () => body,
  }
}

describe('统一请求边界', () => {
  it('同一交互只接受最后一次 GET，前一次会被中止', async () => {
    const pending: Array<{
      signal: AbortSignal
      resolve: (value: ReturnType<typeof jsonResponse>) => void
      reject: (reason: unknown) => void
    }> = []
    vi.stubGlobal('fetch', vi.fn((_path: string, init: RequestInit) =>
      new Promise((resolve, reject) => {
        const signal = init.signal as AbortSignal
        signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true })
        pending.push({ signal, resolve, reject })
      }),
    ))
    const latest = createLatestRequestController()

    const first = latest.run((options) => api.lessons({ page: 1 }, options))
    const second = latest.run((options) => api.lessons({ page: 2 }, options))
    pending[1].resolve(jsonResponse({ items: [], total: 0, page: 2, page_size: 20 }))

    expect(await second).toMatchObject({ page: 2 })
    const firstError = await first.catch((error) => error)
    expect(isRequestCancelled(firstError)).toBe(true)
    expect(pending[0].signal.aborted).toBe(true)
  })

  it('GET 到达 deadline 后中止网络请求', async () => {
    vi.useFakeTimers()
    vi.stubGlobal('fetch', vi.fn((_path: string, init: RequestInit) =>
      new Promise((_resolve, reject) => {
        const signal = init.signal as AbortSignal
        signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true })
      }),
    ))

    const result = request('/api/slow', {}, { timeoutMs: 100 }).catch((error) => error)
    await vi.advanceTimersByTimeAsync(100)

    expect(await result).toBeInstanceOf(RequestTimeoutError)
  })

  it('写请求失败后不自动重试', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ error_code: 'LOGIN_FAILED' }, 503))
    vi.stubGlobal('fetch', fetchMock)

    await expect(api.login({ username: 'ops', password: 'secret' })).rejects.toMatchObject({ status: 503 })
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('教师选项使用受限远程搜索参数', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse([]))
    vi.stubGlobal('fetch', fetchMock)

    await api.teacherOptions({ keyword: 'ana', limit: 30 })

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/teacher-options?keyword=ana&limit=30',
      expect.objectContaining({ credentials: 'include' }),
    )
  })
})
