import { useState } from 'react'
import { Alert, Button, Card, Form, Input, Space, Typography } from 'antd'
import { GlobalOutlined, LockOutlined, RiseOutlined, UserOutlined } from '@ant-design/icons'
import { ApiError, api } from '../api'
import { useI18n } from '../i18n'
import type { OperatorIdentity } from '../types'

const { Paragraph, Text, Title } = Typography

interface LoginFormValues {
  username: string
  password: string
}

interface LoginPageProps {
  onAuthenticated: (operator: OperatorIdentity) => void
}

export default function LoginPage({ onAuthenticated }: LoginPageProps) {
  const { isEnglish, t, toggleLocale } = useI18n()
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')

  async function submit(values: LoginFormValues) {
    setSubmitting(true)
    setError('')
    try {
      onAuthenticated(await api.login(values))
    } catch (reason) {
      setError(reason instanceof ApiError && reason.status === 401
        ? t('账号或密码错误', 'Incorrect username or password')
        : t('登录服务暂时不可用，请稍后重试', 'The login service is temporarily unavailable. Please try again later.'))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <main className="login-page">
      <Card className="login-card">
        <div className="login-language">
          <Button type="text" icon={<GlobalOutlined />} onClick={toggleLocale}>
            {isEnglish ? '中文' : 'EN'}
          </Button>
        </div>
        <Space direction="vertical" size={24} style={{ width: '100%' }}>
          <div>
            <div className="login-brand"><span>T</span><Text strong>{t('新师成长运营台', 'New Teacher Growth')}</Text></div>
            <Title level={2} style={{ margin: '22px 0 6px' }}>{t('欢迎回来', 'Welcome back')}</Title>
            <Paragraph type="secondary" style={{ margin: 0 }}>
              {t('登录后查看新师出营进度、风险与待办处置。', 'Sign in to review readiness progress, risks, and pending actions.')}
            </Paragraph>
          </div>

          {error ? <Alert type="error" showIcon message={error} /> : null}

          <Form<LoginFormValues> layout="vertical" requiredMark={false} onFinish={submit}>
            <Form.Item name="username" label={t('运营账号', 'Operations account')} rules={[{ required: true, message: t('请输入运营账号', 'Enter your operations account') }]}>
              <Input size="large" prefix={<UserOutlined />} autoComplete="username" placeholder={t('请输入账号', 'Enter your account')} />
            </Form.Item>
            <Form.Item name="password" label={t('密码', 'Password')} rules={[{ required: true, message: t('请输入密码', 'Enter your password') }]}>
              <Input.Password size="large" prefix={<LockOutlined />} autoComplete="current-password" placeholder={t('请输入密码', 'Enter your password')} />
            </Form.Item>
            <Button type="primary" htmlType="submit" size="large" block loading={submitting}>
              {t('进入运营台', 'Open workspace')} <RiseOutlined />
            </Button>
          </Form>
        </Space>
      </Card>
    </main>
  )
}
