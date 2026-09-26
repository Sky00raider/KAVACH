import { useCallback, useEffect, useRef, useState } from "react"

/** Polling intervals from CONTRACT §14. */
export const POLL_MS = {
  ingestEvents: 2000,
  queue: 2000,
  requesterRequests: 1500,
} as const

export interface PollState<T> {
  data: T | undefined
  error: Error | undefined
  loading: boolean
  refresh: () => void
}

/**
 * Calls `fn` now and then every `intervalMs` after the previous call settles (no overlapping requests).
 * `intervalMs = null` loads once. Pass `deps` for values `fn` closes over.
 */
export function usePoll<T>(fn: () => Promise<T>, intervalMs: number | null, deps: unknown[] = []): PollState<T> {
  const [data, setData] = useState<T>()
  const [error, setError] = useState<Error>()
  const [loading, setLoading] = useState(true)
  const [tick, setTick] = useState(0)
  const fnRef = useRef(fn)
  fnRef.current = fn

  useEffect(() => {
    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | undefined
    const run = async () => {
      try {
        const value = await fnRef.current()
        if (!cancelled) {
          setData(value)
          setError(undefined)
        }
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e : new Error(String(e)))
      } finally {
        if (!cancelled) {
          setLoading(false)
          if (intervalMs !== null) timer = setTimeout(run, intervalMs)
        }
      }
    }
    run()
    return () => {
      cancelled = true
      clearTimeout(timer)
    }
  }, [intervalMs, tick, ...deps])

  const refresh = useCallback(() => setTick((t) => t + 1), [])
  return { data, error, loading, refresh }
}
