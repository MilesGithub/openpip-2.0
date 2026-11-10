import { queryOptions, useQuery } from '@tanstack/react-query'
import type { QueryKey } from '@tanstack/react-query'
import type { GraphNetworkResponse } from '../../features/graph/types'
import { hasEmptyList, networkWire, normalizeNetworkParams } from './params'
import type { GraphNetworkParams } from './params'
import {
  GRAPH_DATA_STALE_MS,
  GRAPH_QUERY_BASE,
  graphData,
  keepGraphPreviousData,
  useGraphViewer,
} from './request'
import type { GraphHookOptions, GraphViewer } from './request'

const NETWORK_PATH = '/graph/v1/network'

export function graphNetworkOptions(viewer: GraphViewer, params: GraphNetworkParams) {
  const normalized = normalizeNetworkParams(params)
  const queryKey: QueryKey = ['graph', 'network', viewer, normalized]
  return queryOptions({
    ...GRAPH_QUERY_BASE,
    queryKey,
    queryFn: () => graphData<GraphNetworkResponse>(NETWORK_PATH, networkWire(normalized)),
    staleTime: GRAPH_DATA_STALE_MS,
    enabled: !hasEmptyList(normalized),
    meta: { graphViewer: viewer },
  })
}

export function useGraphNetwork(
  params: GraphNetworkParams,
  options: GraphHookOptions<GraphNetworkResponse> = {},
) {
  const viewer = useGraphViewer()
  const base = graphNetworkOptions(viewer, params)
  const { keepPrevious, retry, enabled } = options
  return useQuery({
    ...base,
    retry: retry ?? base.retry,
    enabled: base.enabled && (enabled ?? true),
    placeholderData: keepPrevious ? keepGraphPreviousData<GraphNetworkResponse>(viewer) : undefined,
  })
}
