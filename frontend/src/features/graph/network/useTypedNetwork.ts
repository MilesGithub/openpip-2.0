import { useEffect, useMemo, useState } from 'react'
import type { UseQueryResult } from '@tanstack/react-query'
import { useGraphNetwork } from '../../../api/graph/network'
import { hasEmptyList, normalizeNetworkParams } from '../../../api/graph/params'
import type { GraphNetworkParams } from '../../../api/graph/params'
import type { GraphNetworkResponse } from '../types'
import { graphOfNetwork } from './graph'
import type { TypedGraph } from './graph'
import { networkView } from './networkView'
import type { NetworkViewState } from './networkView'

// The result's error type is unknown: useGraphNetwork's is Error under the strict test's
// options and unknown under the project's, and unknown accepts both.
export interface UseTypedNetworkOptions { debounceMs?: number; enabled?: boolean }
export interface TypedNetworkResult {
  state: NetworkViewState; graph: TypedGraph | null; error: unknown
  result: UseQueryResult<GraphNetworkResponse, unknown>
}

/**
 * The body is held through the client's `keepPrevious` while the params change: React
 * Query's keepPreviousData would carry a staff body across a logout, and a `select`
 * would cost the edges and nodes their identity over equal refetches. The query waits
 * until the params have stood still for `debounceMs`, since a key whose last answer was
 * a 503 is sent again whenever the params return to it. The state reads the current
 * params, so params just chosen never read as disabled: they read as loading or
 * updating, or, while the debounce runs after an error, as that error, which answers
 * the settled params; params just cleared read as disabled at once.
 */
export function useTypedNetwork(params: GraphNetworkParams | null, options: UseTypedNetworkOptions = {}): TypedNetworkResult {
  const debounceMs = options.debounceMs ?? 300
  const normalized = params ? normalizeNetworkParams(params) : null
  const key = normalized === null ? null : JSON.stringify(normalized)
  const [debounced, setDebounced] = useState<string | null>(key)
  useEffect(() => {
    if (debounceMs <= 0) return
    const timer = setTimeout(() => setDebounced(key), debounceMs)
    return () => clearTimeout(timer)
  }, [key, debounceMs])
  const settled = debounceMs <= 0 ? key : debounced
  const settledParams = settled === null ? null : (JSON.parse(settled) as GraphNetworkParams)
  const settledDisabled =
    settledParams === null || hasEmptyList(normalizeNetworkParams(settledParams)) || options.enabled === false
  const result = useGraphNetwork(settledParams ?? { seeds: [] }, { keepPrevious: true, enabled: !settledDisabled })
  const disabled = normalized === null || hasEmptyList(normalized) || options.enabled === false
  const view = networkView({ disabled, debouncing: settled !== key, result })
  const graph = useMemo(() => (view.response ? graphOfNetwork(view.response) : null), [view.response])
  return { state: view.state, graph, error: view.error, result }
}
