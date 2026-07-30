import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

const nginx = readFileSync(new URL('../nginx.conf', import.meta.url), 'utf8')

describe('Nginx 性能与登录保护', () => {
  it('复用上游连接、缓冲普通 JSON 并启用 gzip', () => {
    expect(nginx).toContain('keepalive 32;')
    expect(nginx).toContain('proxy_set_header Connection "";')
    expect(nginx).toContain('proxy_buffering on;')
    expect(nginx).toContain('gzip on;')
    expect(nginx).toContain('application/json')
  })

  it('不信任入站转发头，只把直接网关规范成单一代理跳数', () => {
    expect(nginx).not.toContain('limit_req')
    expect(nginx).toContain('proxy_set_header Host $host;')
    expect(nginx).toContain('proxy_set_header X-Real-IP $remote_addr;')
    expect(nginx).toContain('proxy_set_header X-Forwarded-For $remote_addr;')
    expect(nginx).toContain('proxy_set_header X-Forwarded-Proto https;')
    expect(nginx).not.toContain('$http_x_real_ip')
    expect(nginx).not.toContain('$http_x_forwarded_proto')
    expect(nginx).not.toContain('$proxy_add_x_forwarded_for')
  })
})
