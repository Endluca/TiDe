import { useCallback, useEffect, useRef } from 'react'
import { createLatestRequestController } from './api'
import type { LatestRequestController } from './api'

export function useLatestRequest(): LatestRequestController {
  const controllerRef = useRef<LatestRequestController | null>(null)
  if (!controllerRef.current) controllerRef.current = createLatestRequestController()

  useEffect(() => {
    const controller = controllerRef.current
    return () => controller?.cancel()
  }, [])

  return controllerRef.current
}

export interface LatestRequestControllerMap {
  (key: string): LatestRequestController
  cancelAll(): void
}

export function useLatestRequestMap(): LatestRequestControllerMap {
  const controllersRef = useRef(new Map<string, LatestRequestController>())
  const cancelAll = useCallback(() => {
    controllersRef.current.forEach((controller) => controller.cancel())
    controllersRef.current.clear()
  }, [])

  useEffect(() => {
    return cancelAll
  }, [cancelAll])

  const forKey = useCallback((key: string) => {
    const current = controllersRef.current.get(key)
    if (current) return current
    const controller = createLatestRequestController()
    controllersRef.current.set(key, controller)
    return controller
  }, [])

  return Object.assign(forKey, { cancelAll })
}
