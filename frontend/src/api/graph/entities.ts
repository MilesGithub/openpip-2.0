import { queryOptions, useQuery } from '@tanstack/react-query'
import type { QueryKey } from '@tanstack/react-query'
import type { GraphEntityRef, GraphEntityResponse } from '../../features/graph/types'
import { hasEmptyList, isGraphPathSegment, normalizeSelection, selectionWire } from './params'
import type { GraphSelection } from './params'
import {
  GRAPH_DATA_STALE_MS,
  GRAPH_QUERY_BASE,
  graphData,
  keepGraphPreviousData,
  useGraphViewer,
} from './request'
import type { GraphHookOptions, GraphViewer } from './request'

function entityPath(entityId: GraphEntityRef): string {
  return `/graph/v1/entities/${encodeURIComponent(entityId)}`
}

export function graphEntityOptions(
  viewer: GraphViewer,
  entityId: GraphEntityRef,
  selection: GraphSelection = {},
) {
  const normalized = normalizeSelection(selection ?? {})
  const queryKey: QueryKey = ['graph', 'entity', entityId, viewer, normalized]
  return queryOptions({
    ...GRAPH_QUERY_BASE,
    queryKey,
    queryFn: () => graphData<GraphEntityResponse>(entityPath(entityId), selectionWire(normalized)),
    staleTime: GRAPH_DATA_STALE_MS,
    enabled: isGraphPathSegment(entityId) && !hasEmptyList(normalized),
    meta: { graphViewer: viewer },
  })
}

/** A merged entity answers 301 and the browser follows it, so `data.entity.id` can differ from `entityId`: read the response's id. */
export function useGraphEntity(
  entityId: GraphEntityRef,
  selection: GraphSelection = {},
  options: GraphHookOptions<GraphEntityResponse> = {},
) {
  const viewer = useGraphViewer()
  const base = graphEntityOptions(viewer, entityId, selection)
  const { keepPrevious, retry, enabled } = options
  return useQuery({
    ...base,
    retry: retry ?? base.retry,
    enabled: base.enabled && (enabled ?? true),
    placeholderData: keepPrevious ? keepGraphPreviousData<GraphEntityResponse>(viewer) : undefined,
  })
}
