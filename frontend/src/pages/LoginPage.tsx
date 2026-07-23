import { useState } from 'react'
import { Alert, Button, Card, Form, Input, Space, Typography } from 'antd'
import { LockOutlined, RiseOutlined, UserOutlined } from '@ant-design/icons'
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
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')

  async function submit(values: LoginFormValues) {
    setSubmitting(true)
    setError('')
    try {
      const response = await fetch('/api/auth/login', {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(values),
      })
      if (!response.ok) {
        setError(response.status === 401 ? '账号或密码错误' : '登录服务暂时不可用，请稍后重试')
        return
      }
      onAuthenticated(await response.json() as OperatorIdentity)
    } catch {
      setError('登录服务暂时不可用，请稍后重试')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <main className="login-page">
      <Card className="login-card">
        <Space direction="vertical" size={24} style={{ width: '100%' }}>
          <div>
            <div className="login-brand"><span>T</span><Text strong>新师成长运营台</Text></div>
            <Title level={2} style={{ margin: '22px 0 6px' }}>欢迎回来</Title>
            <Paragraph type="secondary" style={{ margin: 0 }}>
              登录后查看新师出营进度、风险与待办处置。
            </Paragraph>
          </div>

          {error ? <Alert type="error" showIcon message={error} /> : null}

          <Form<LoginFormValues> layout="vertical" requiredMark={false} onFinish={submit}>
            <Form.Item name="username" label="运营账号" rules={[{ required: true, message: '请输入运营账号' }]}>
              <Input size="large" prefix={<UserOutlined />} autoComplete="username" placeholder="请输入账号" />
            </Form.Item>
            <Form.Item name="password" label="密码" rules={[{ required: true, message: '请输入密码' }]}>
              <Input.Password size="large" prefix={<LockOutlined />} autoComplete="current-password" placeholder="请输入密码" />
            </Form.Item>
            <Button type="primary" htmlType="submit" size="large" block loading={submitting}>
              进入运营台 <RiseOutlined />
            </Button>
          </Form>
        </Space>
      </Card>
    </main>
  )
}
