import { queryOptions, useQuery } from '@tanstack/react-query'
import type { QueryKey } from '@tanstack/react-query'
import type { GraphFeatureName, GraphStatus } from '../../features/graph/types'
import { GraphBadBodyError } from './errors'
import { GRAPH_QUERY_BASE, graphGet } from './request'
import type { GraphCallerFreeHookOptions } from './request'

const STATUS_PATH = '/graph/v1/status'

export const GRAPH_STATUS_STALE_MS = 5 * 60 * 1000

export type GraphFeatureState = 'loading' | 'unknown' | 'off' | 'on'

function isStatusBody(data: unknown): data is GraphStatus {
  if (typeof data !== 'object' || data === null) return false
  const body = data as { typed_graph?: unknown; features?: unknown }
  return (
    typeof body.typed_graph === 'boolean' &&
    typeof body.features === 'object' &&
    body.features !== null &&
    !Array.isArray(body.features)
  )
}

/** axios resolves every 2xx, and an HTML fallback page is a 200, so both are checked. */
async function fetchGraphStatus(): Promise<GraphStatus> {
  const response = await graphGet<unknown>(STATUS_PATH)
  if (response.status !== 200 || !isStatusBody(response.data)) {
    throw new GraphBadBodyError(response.status)
  }
  return response.data
}

export function graphStatusOptions() {
  const queryKey: QueryKey = ['graph', 'status']
  return queryOptions({
    ...GRAPH_QUERY_BASE,
    queryKey,
    queryFn: fetchGraphStatus,
    staleTime: GRAPH_STATUS_STALE_MS,
    // Re-read every 5 minutes by each mounted observer while the tab is visible, on a route with no graph budget (views.py:164-168).
    refetchInterval: GRAPH_STATUS_STALE_MS,
  })
}

export function useGraphStatus(options: GraphCallerFreeHookOptions<GraphStatus> = {}) {
  const base = graphStatusOptions()
  return useQuery({ ...base, ...options, retry: options.retry ?? base.retry })
}

/**
 * Anything but a well-formed 200 is `unknown`, never `off`. A failed refetch
 * keeps the last 200's `on`, so a 429 does not flip a nav link, and says
 * `unknown` in place of its `off`, the one state that may say "not enabled".
 */
export function graphFeatureState(
  result: { data?: GraphStatus; isLoading: boolean; isRefetchError?: boolean },
  name: GraphFeatureName,
): GraphFeatureState {
  if (result.data) {
    const value: unknown = (result.data.features as Record<string, unknown>)[name]
    if (value === true) return 'on'
    if (value === false) return result.isRefetchError ? 'unknown' : 'off'
    return 'unknown'
  }
  return result.isLoading ? 'loading' : 'unknown'
}

export function useGraphFeature(name: GraphFeatureName): GraphFeatureState {
  return graphFeatureState(useGraphStatus(), name)
}
