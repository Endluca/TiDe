import AntdBoundary from './AntdBoundary'
import LoginPage from './pages/LoginPage'
import type { OperatorIdentity } from './types'

export default function LoginApplication({
  onAuthenticated,
}: {
  onAuthenticated: (operator: OperatorIdentity) => void
}) {
  return (
    <AntdBoundary>
      <LoginPage onAuthenticated={onAuthenticated} />
    </AntdBoundary>
  )
}
