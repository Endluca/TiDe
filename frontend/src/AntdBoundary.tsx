import type { ReactNode } from 'react'
import { App as AntdApp, ConfigProvider } from 'antd'
import enUS from 'antd/locale/en_US'
import zhCN from 'antd/locale/zh_CN'
import dayjs from 'dayjs'
import 'dayjs/locale/en'
import 'dayjs/locale/zh-cn'
import { useI18n } from './i18n'

export default function AntdBoundary({ children }: { children: ReactNode }) {
  const { locale } = useI18n()
  dayjs.locale(locale === 'en-US' ? 'en' : 'zh-cn')

  return (
    <ConfigProvider
      locale={locale === 'en-US' ? enUS : zhCN}
      theme={{
        token: {
          colorPrimary: '#2f745c',
          colorInfo: '#52796a',
          colorSuccess: '#2f7c61',
          colorWarning: '#b77b2e',
          colorError: '#bd5949',
          colorText: '#1d2a25',
          colorTextSecondary: '#66746d',
          colorBgLayout: '#f4f5f1',
          colorBorderSecondary: '#e5e9e5',
          borderRadius: 9,
          borderRadiusLG: 12,
          fontFamily:
            '-apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif',
        },
        components: {
          Card: { headerBg: 'transparent' },
          Layout: { bodyBg: '#f4f5f1', headerBg: '#ffffff' },
          Table: { headerBg: '#f6f8f5', rowHoverBg: '#f7faf8' },
          Menu: {
            darkItemBg: '#17241f',
            darkSubMenuItemBg: '#17241f',
            darkItemSelectedBg: '#d8ebe2',
          },
        },
      }}
    >
      <AntdApp>{children}</AntdApp>
    </ConfigProvider>
  )
}
