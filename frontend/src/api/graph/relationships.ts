import { infiniteQueryOptions, queryOptions, useInfiniteQuery, useQuery } from '@tanstack/react-query'
import type { InfiniteData, QueryKey } from '@tanstack/react-query'
import type {
  GraphEvidenceRef,
  GraphEvidenceResponse,
  GraphRelationshipRef,
  GraphRelationshipResponse,
} from '../../features/graph/types'
import { GraphBadBodyError } from './errors'
import {
  evidenceWire,
  hasEmptyList,
  isGraphPathSegment,
  normalizeEvidenceParams,
  normalizeRelationshipParams,
  relationshipWire,
} from './params'
import type { GraphEvidenceParams, GraphRelationshipParams } from './params'
import {
  GRAPH_DATA_STALE_MS,
  GRAPH_QUERY_BASE,
  graphData,
  keepGraphPreviousData,
  useGraphViewer,
} from './request'
import type { GraphHookOptions, GraphInfiniteHookOptions, GraphViewer } from './request'

function relationshipPath(ref: GraphRelationshipRef): string {
  return `/graph/v1/relationships/${encodeURIComponent(ref)}`
}

export function graphRelationshipOptions(
  viewer: GraphViewer,
  ref: GraphRelationshipRef,
  filters: GraphRelationshipParams = {},
) {
  const normalized = normalizeRelationshipParams(filters ?? {})
  const queryKey: QueryKey = ['graph', 'relationship', ref, 'page', viewer, normalized]
  return queryOptions({
    ...GRAPH_QUERY_BASE,
    queryKey,
    queryFn: () => graphData<GraphRelationshipResponse>(relationshipPath(ref), relationshipWire(normalized)),
    staleTime: GRAPH_DATA_STALE_MS,
    enabled: isGraphPathSegment(ref) && !hasEmptyList(normalized),
    meta: { graphViewer: viewer },
  })
}

export function useGraphRelationship(
  ref: GraphRelationshipRef,
  filters: GraphRelationshipParams = {},
  options: GraphHookOptions<GraphRelationshipResponse> = {},
) {
  const viewer = useGraphViewer()
  const base = graphRelationshipOptions(viewer, ref, filters)
  const { keepPrevious, retry, enabled } = options
  return useQuery({
    ...base,
    retry: retry ?? base.retry,
    enabled: base.enabled && (enabled ?? true),
    placeholderData: keepPrevious ? keepGraphPreviousData<GraphRelationshipResponse>(viewer) : undefined,
  })
}

function evidencePath(ref: GraphRelationshipRef): string {
  return `/graph/v1/relationships/${encodeURIComponent(ref)}/evidence`
}

/**
 * Keyset pages (W19): `after` is the page param, never in the key, and is sent
 * only from the second page on. The end is page.next being null, never a row
 * count: a page can end early, and can hold no rows with a next.
 */
export function graphEvidenceOptions(
  viewer: GraphViewer,
  ref: GraphRelationshipRef,
  filters: GraphEvidenceParams = {},
) {
  const normalized = normalizeEvidenceParams(filters ?? {})
  const queryKey: QueryKey = ['graph', 'relationship', ref, 'evidence', viewer, normalized]
  return infiniteQueryOptions({
    ...GRAPH_QUERY_BASE,
    queryKey,
    queryFn: ({ pageParam }) =>
      graphData<GraphEvidenceResponse>(evidencePath(ref), evidenceWire(normalized, pageParam)).then((body) => {
        // getNextPageParam reads page.next as the observer updates, where a throw never becomes an error state.
        if (typeof body.page !== 'object' || body.page === null || Array.isArray(body.page)) throw new GraphBadBodyError(200)
        return body
      }),
    initialPageParam: null as GraphEvidenceRef | null,
    getNextPageParam: (last) => last.page.next,
    staleTime: GRAPH_DATA_STALE_MS,
    enabled: isGraphPathSegment(ref) && !hasEmptyList(normalized),
    meta: { graphViewer: viewer },
  })
}

export function useGraphEvidence(
  ref: GraphRelationshipRef,
  filters: GraphEvidenceParams = {},
  options: GraphInfiniteHookOptions<GraphEvidenceResponse, GraphEvidenceRef | null> = {},
) {
  const viewer = useGraphViewer()
  const base = graphEvidenceOptions(viewer, ref, filters)
  const { keepPrevious, retry, enabled } = options
  return useInfiniteQuery({
    ...base,
    retry: retry ?? base.retry,
    enabled: base.enabled && (enabled ?? true),
    placeholderData: keepPrevious
      ? keepGraphPreviousData<InfiniteData<GraphEvidenceResponse, GraphEvidenceRef | null>>(viewer)
      : undefined,
  })
}
