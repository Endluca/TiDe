import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'

export type AppLocale = 'zh-CN' | 'en-US'

export const LANGUAGE_STORAGE_KEY = 'tide-ui-language'

export function normalizeLocale(value: string | null | undefined): AppLocale {
  return value === 'en-US' ? 'en-US' : 'zh-CN'
}

interface I18nContextValue {
  locale: AppLocale
  isEnglish: boolean
  setLocale: (locale: AppLocale) => void
  toggleLocale: () => void
  t: (chinese: string, english: string) => string
}

const I18nContext = createContext<I18nContextValue | null>(null)

function initialLocale(): AppLocale {
  if (typeof window === 'undefined') return 'zh-CN'
  try {
    return normalizeLocale(window.localStorage.getItem(LANGUAGE_STORAGE_KEY))
  } catch {
    return 'zh-CN'
  }
}

export function I18nProvider({ children }: { children: ReactNode }) {
  const [locale, setLocaleState] = useState<AppLocale>(initialLocale)

  const setLocale = useCallback((nextLocale: AppLocale) => {
    setLocaleState(nextLocale)
    try {
      window.localStorage.setItem(LANGUAGE_STORAGE_KEY, nextLocale)
    } catch {
      // Language switching still works when local storage is unavailable.
    }
  }, [])

  const toggleLocale = useCallback(() => {
    setLocale(locale === 'zh-CN' ? 'en-US' : 'zh-CN')
  }, [locale, setLocale])

  const t = useCallback(
    (chinese: string, english: string) => (locale === 'en-US' ? english : chinese),
    [locale],
  )

  useEffect(() => {
    document.documentElement.lang = locale
    document.title = t('新师成长运营台', 'New Teacher Growth Operations')
  }, [locale, t])

  const value = useMemo<I18nContextValue>(
    () => ({
      locale,
      isEnglish: locale === 'en-US',
      setLocale,
      toggleLocale,
      t,
    }),
    [locale, setLocale, t, toggleLocale],
  )

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>
}

export function useI18n(): I18nContextValue {
  const context = useContext(I18nContext)
  if (!context) throw new Error('useI18n must be used inside I18nProvider')
  return context
}
