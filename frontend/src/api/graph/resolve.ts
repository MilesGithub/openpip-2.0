import { queryOptions, useQuery } from '@tanstack/react-query'
import type { QueryKey } from '@tanstack/react-query'
import type { GraphResolveResponse } from '../../features/graph/types'
import { hasEmptyList, normalizeResolveParams, resolveWire } from './params'
import type { GraphResolveParams } from './params'
import {
  GRAPH_DATA_STALE_MS,
  GRAPH_MAX_REQUEST_LINE,
  GRAPH_QUERY_BASE,
  graphData,
  graphRequestLineBytes,
  keepGraphPreviousData,
  useGraphViewer,
} from './request'
import type { GraphHookOptions, GraphViewer } from './request'

const RESOLVE_PATH = '/graph/v1/resolve'

/** The cap `status.limits.resolve_max_terms` publishes; a caller may pass the live value. */
export const GRAPH_RESOLVE_MAX_TERMS = 25

/**
 * Splits terms into request-sized chunks, order kept: each chunk holds at most
 * maxTerms terms and its request line fits GRAPH_MAX_REQUEST_LINE. useGraphResolve
 * sends one request and does not split; X1 sends the chunks through useQueries with
 * graphResolveOptions. A term too long for any line is left alone in its chunk,
 * and the GET helper refuses it as `too_long`. A term that cannot be encoded is
 * left alone too, and its query fails as `unknown`.
 */
export function chunkResolveTerms(
  params: GraphResolveParams,
  maxTerms: number = GRAPH_RESOLVE_MAX_TERMS,
): string[][] {
  const normalized = normalizeResolveParams(params)
  // A state the hook treats as disabled: an empty list would make the wire builder throw.
  if (hasEmptyList(normalized)) return normalized.q.length > 0 ? [normalized.q] : []
  const fits = (terms: string[]) => {
    try {
      return graphRequestLineBytes(RESOLVE_PATH, resolveWire({ ...normalized, q: terms })) <= GRAPH_MAX_REQUEST_LINE
    } catch (error) {
      // An unpaired surrogate has no percent-encoding: alone in its chunk, its query reports it.
      if (error instanceof URIError) return false
      throw error
    }
  }
  const chunks: string[][] = []
  let chunk: string[] = []
  for (const term of normalized.q) {
    const next = [...chunk, term]
    if (chunk.length === 0 || (next.length <= maxTerms && fits(next))) {
      chunk = next
    } else {
      chunks.push(chunk)
      chunk = [term]
    }
  }
  if (chunk.length > 0) chunks.push(chunk)
  return chunks
}

export function graphResolveOptions(viewer: GraphViewer, params: GraphResolveParams) {
  const normalized = normalizeResolveParams(params)
  const queryKey: QueryKey = ['graph', 'resolve', viewer, normalized]
  return queryOptions({
    ...GRAPH_QUERY_BASE,
    queryKey,
    queryFn: () => graphData<GraphResolveResponse>(RESOLVE_PATH, resolveWire(normalized)),
    staleTime: GRAPH_DATA_STALE_MS,
    enabled: !hasEmptyList(normalized),
    meta: { graphViewer: viewer },
  })
}

export function useGraphResolve(
  params: GraphResolveParams,
  options: GraphHookOptions<GraphResolveResponse> = {},
) {
  const viewer = useGraphViewer()
  const base = graphResolveOptions(viewer, params)
  const { keepPrevious, retry, enabled } = options
  return useQuery({
    ...base,
    retry: retry ?? base.retry,
    enabled: base.enabled && (enabled ?? true),
    placeholderData: keepPrevious ? keepGraphPreviousData<GraphResolveResponse>(viewer) : undefined,
  })
}
