import { useEffect, useEffectEvent, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { useLocation } from 'react-router-dom'
import { classifyGraphError } from '../../../api/graph/errors'
import { graphStatusOptions } from '../../../api/graph/status'

export interface RetryControl { offered: boolean; busy: boolean; onRetry: () => void }
export function useRetry(
  query: { refetch: () => Promise<unknown>; isFetching: boolean; isPaused: boolean },
  key: string,
  errorOffers: boolean,
): RetryControl {
  // A query with no data that refetches has its error cleared and turns pending, so the pressed button
  // is kept while its own fetch runs, busy, and never leaves under the user's focus (J13).
  const [pressed, setPressed] = useState<string | null>(null)
  const running = query.isFetching || query.isPaused
  const kept = pressed === key && running
  // Guarded and during render, since a state set in an effect fails lint.
  if (pressed !== null && !kept) setPressed(null)
  return {
    offered: errorOffers || kept,
    busy: running,
    onRetry: () => {
      if (running) return
      setPressed(key)
      void query.refetch()
    },
  }
}

export function useStatusRecheck(query: { error: unknown; errorUpdatedAt: number }): void {
  // With the switch off every typed route but `status` answers 404, so a `not_found` reads `status` once
  // more. Each failed fetch has its own errorUpdatedAt, so a repeat rechecks once and never loops, and the
  // cached query is refetched, which starts no observer and no timer.
  const client = useQueryClient()
  const checked = useRef(0)
  const notFound = query.error !== null && query.error !== undefined && classifyGraphError(query.error).kind === 'not_found'
  useEffect(() => {
    if (notFound && checked.current !== query.errorUpdatedAt) {
      checked.current = query.errorUpdatedAt
      void client.refetchQueries({ queryKey: graphStatusOptions().queryKey, exact: true })
    }
  }, [client, notFound, query.errorUpdatedAt])
}

export function isArrival(locationKey: string): boolean {
  // React Router keys a fresh document's first location 'default' and every client-side navigation anew; a
  // reload keeps its entry's key in history.state, so it counts as an arrival.
  return locationKey !== 'default'
}

export function useArrivalScroll(): void {
  // Read through an effect event, so the effect runs once, on mount: a change of the filters, the search or
  // the seeds keeps the page mounted and does not scroll, and a first location keyed 'default' stays where the
  // browser put it.
  const arrived = isArrival(useLocation().key)
  const scroll = useEffectEvent(() => {
    if (arrived) window.scrollTo(0, 0)
  })
  useEffect(() => {
    scroll()
  }, [])
}
