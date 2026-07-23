import React from 'react'
import ReactDOM from 'react-dom/client'
import { App as AntdApp, ConfigProvider } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import dayjs from 'dayjs'
import 'dayjs/locale/zh-cn'
import RootApp from './App'
import './styles.css'

dayjs.locale('zh-cn')

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ConfigProvider
      locale={zhCN}
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
      <AntdApp>
        <RootApp />
      </AntdApp>
    </ConfigProvider>
  </React.StrictMode>,
)
