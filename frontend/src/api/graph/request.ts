import { useSyncExternalStore } from 'react'
import type {
  InfiniteData,
  QueryKey,
  UseInfiniteQueryOptions,
  UseQueryOptions,
} from '@tanstack/react-query'
import type { AxiosResponse, ParamsSerializerOptions } from 'axios'
import { useAuthStore } from '../../store/authStore'
import { apiClient } from '../client'
import {
  GraphBadBodyError,
  GraphRequestTooLongError,
  graphMayResend,
  retryGraphQuery,
} from './errors'
import type { GraphWireParams } from './params'

/**
 * The longest request line the shipped gunicorn accepts: past it the server's
 * own HTML 400 or 414 answers, or, behind HTTP/2, a closed connection with no
 * status at all (docs/developer/api/typed-graph.md, Errors). It is not in
 * status.limits, and OPEN_QUESTIONS.md A20 may move it: this is the one place.
 */
export const GRAPH_MAX_REQUEST_LINE = 4_094

/** Repeated keys for the one array left on the wire, `score`. */
export const GRAPH_PARAMS_SERIALIZER: ParamsSerializerOptions = { indexes: null }

/** Every route but status and vocabularies: a page opened or re-queried more than 60 s later sees a withdrawal; a page left open refetches only on a reconnect, once stale. */
export const GRAPH_DATA_STALE_MS = 60 * 1000

/**
 * Spread into every typed query. No refetch on focus: for evidence it would
 * re-read every loaded page. A 503 is not sent again by a remount or a
 * reconnect; a consumer's `refetch` still sends it, and `enabled` going from
 * false to true does too, which no option stops.
 */
export const GRAPH_QUERY_BASE = {
  retry: retryGraphQuery,
  retryOnMount: graphMayResend,
  refetchOnMount: graphMayResend,
  refetchOnReconnect: graphMayResend,
  refetchOnWindowFocus: false,
} as const

/**
 * Who a response is for. `account` is a logged-in non-staff caller: the API
 * answers it as a visitor, but a claim older than the account can make it staff,
 * so it never shares a cache entry with the logged-out.
 */
export type GraphViewer = 'staff' | 'account' | 'visitor'

/** Read from what the request will carry (client.ts:25), not from the tab's store. */
function currentGraphViewer(): GraphViewer {
  const token = localStorage.getItem('openpip_access_token')
  if (!token) return 'visitor'
  try {
    return JSON.parse(atob(token.split('.')[1])).is_admin ? 'staff' : 'account'
  } catch {
    return 'account'
  }
}

function subscribeGraphViewer(onChange: () => void): () => void {
  window.addEventListener('storage', onChange)
  // A page restored from the back/forward cache may have missed the storage event.
  window.addEventListener('pageshow', onChange)
  const stop = useAuthStore.subscribe(onChange)
  return () => {
    window.removeEventListener('storage', onChange)
    window.removeEventListener('pageshow', onChange)
    stop()
  }
}

export function useGraphViewer(): GraphViewer {
  return useSyncExternalStore(subscribeGraphViewer, currentGraphViewer)
}

/** The options of `status` and `vocabularies`, which do not vary by caller. */
export type GraphCallerFreeHookOptions<TData> = Pick<
  UseQueryOptions<TData, Error, TData, QueryKey>,
  'retry'
> & { enabled?: boolean }

/** What a plain hook's caller may set beyond the factory's options. `keepPrevious` holds the last body while a filter changes. */
export type GraphHookOptions<TData> = GraphCallerFreeHookOptions<TData> & { keepPrevious?: boolean }

/** The same for the one infinite hook. */
export type GraphInfiniteHookOptions<TData, TPageParam> = Pick<
  UseInfiniteQueryOptions<TData, Error, InfiniteData<TData>, QueryKey, TPageParam>,
  'retry'
> & { enabled?: boolean; keepPrevious?: boolean }

/** `placeholderData` for a typed hook: the last body while a filter changes, never one fetched for another caller. */
export function keepGraphPreviousData<T>(viewer: GraphViewer) {
  return (previous: T | undefined, previousQuery: { meta?: Record<string, unknown> } | undefined): T | undefined =>
    previousQuery?.meta?.graphViewer === viewer ? previous : undefined
}

/** The bytes of the request line this request would send. */
export function graphRequestLineBytes(url: string, params: GraphWireParams = {}): number {
  const uri = apiClient.getUri({ url, params, paramsSerializer: GRAPH_PARAMS_SERIALIZER })
  const { pathname, search } = new URL(uri, window.location.origin)
  // pathname and search are percent-encoded, so every character is one byte.
  return `GET ${pathname}${search} HTTP/1.1`.length
}

export function graphGet<T>(url: string, params: GraphWireParams = {}): Promise<AxiosResponse<T>> {
  const bytes = graphRequestLineBytes(url, params)
  if (bytes > GRAPH_MAX_REQUEST_LINE) {
    return Promise.reject(new GraphRequestTooLongError(bytes, GRAPH_MAX_REQUEST_LINE))
  }
  return apiClient.get<T>(url, { params, paramsSerializer: GRAPH_PARAMS_SERIALIZER })
}

export function graphData<T>(url: string, params: GraphWireParams = {}): Promise<T> {
  return graphGet<T>(url, params).then(({ data, status }) => {
    if (status !== 200 || typeof data !== 'object' || data === null || Array.isArray(data)) {
      throw new GraphBadBodyError(status)
    }
    return data
  })
}
