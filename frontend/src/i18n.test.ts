import { describe, expect, it } from 'vitest'
import { LANGUAGE_STORAGE_KEY, normalizeLocale } from './i18n'

describe('中英文切换基础约束', () => {
  it('默认中文，只接受受控的英文语言值', () => {
    expect(normalizeLocale(undefined)).toBe('zh-CN')
    expect(normalizeLocale('zh-CN')).toBe('zh-CN')
    expect(normalizeLocale('en-US')).toBe('en-US')
    expect(normalizeLocale('fr-FR')).toBe('zh-CN')
  })

  it('使用稳定且只属于界面的本地存储键', () => {
    expect(LANGUAGE_STORAGE_KEY).toBe('tide-ui-language')
  })
})
