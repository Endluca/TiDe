import { lazy, Suspense, useCallback, useEffect, useState } from 'react'
import { api, isRequestCancelled } from './api'
import type { OperatorIdentity } from './types'

const LoginApplication = lazy(() => import('./LoginApplication'))
const WorkspaceApplication = lazy(() => import('./WorkspaceApplication'))

function BootstrapLoading() {
  return (
    <div className="bootstrap-loading" role="status" aria-live="polite">
      <span className="bootstrap-loading-mark">T</span>
    </div>
  )
}

export default function App() {
  const [operator, setOperator] = useState<OperatorIdentity | null>()
  const handleSignedOut = useCallback(() => setOperator(null), [])

  useEffect(() => {
    const controller = new AbortController()
    api.me({ signal: controller.signal })
      .then(setOperator)
      .catch((error) => {
        if (!isRequestCancelled(error)) setOperator(null)
      })
    return () => controller.abort()
  }, [])

  if (operator === undefined) return <BootstrapLoading />

  return (
    <Suspense fallback={<BootstrapLoading />}>
      {operator
        ? <WorkspaceApplication initialOperator={operator} onSignedOut={handleSignedOut} />
        : <LoginApplication onAuthenticated={setOperator} />}
    </Suspense>
  )
}
