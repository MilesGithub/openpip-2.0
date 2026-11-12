import type { UseQueryResult } from '@tanstack/react-query'
import type { GraphNetworkResponse } from '../types'

export type NetworkViewState = 'disabled' | 'loading' | 'updating' | 'ready' | 'error'
export interface NetworkViewInput {
  disabled: boolean
  debouncing: boolean
  result: Pick<UseQueryResult<GraphNetworkResponse, unknown>, 'data' | 'status' | 'fetchStatus' | 'isPlaceholderData' | 'error'>
}
export interface NetworkView { state: NetworkViewState; response: GraphNetworkResponse | null; error: unknown }

/**
 * The first rule that applies. React Query reports a disabled key's kept body as a success, so
 * `disabled` comes first, and a retry of an errored key shows progress. An error keeps only its
 * own key's body, as a failed refetch does: placeholder data answers another request. A
 * placeholder can be the body of a key older than the previous one; `graph.query` says which.
 */
export function networkView(input: NetworkViewInput): NetworkView {
  const { disabled, debouncing, result } = input
  if (disabled) return { state: 'disabled', response: null, error: null }
  const data = result.data === undefined || result.data === null ? null : result.data
  if (result.status === 'error' && result.fetchStatus === 'fetching') {
    return data === null ? { state: 'loading', response: null, error: null } : { state: 'updating', response: data, error: null }
  }
  if (result.status === 'error') {
    return { state: 'error', response: data !== null && !result.isPlaceholderData ? data : null, error: result.error }
  }
  if (data === null) return { state: 'loading', response: null, error: null }
  if (debouncing || result.isPlaceholderData || result.fetchStatus === 'fetching') {
    return { state: 'updating', response: data, error: null }
  }
  return { state: 'ready', response: data, error: null }
}
