import AntdBoundary from './AntdBoundary'
import WorkspaceApp from './WorkspaceApp'
import type { OperatorIdentity } from './types'

export default function WorkspaceApplication({
  initialOperator,
  onSignedOut,
}: {
  initialOperator: OperatorIdentity
  onSignedOut: () => void
}) {
  return (
    <AntdBoundary>
      <WorkspaceApp initialOperator={initialOperator} onSignedOut={onSignedOut} />
    </AntdBoundary>
  )
}
