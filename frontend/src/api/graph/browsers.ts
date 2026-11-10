import { queryOptions, useQuery } from '@tanstack/react-query'
import type { QueryKey } from '@tanstack/react-query'
import type { GraphBrowserResponse, GraphBrowsersResponse } from '../../features/graph/types'
import { browsersWire, isGraphPathSegment, normalizeBrowsersParams } from './params'
import type { GraphBrowsersParams } from './params'
import {
  GRAPH_DATA_STALE_MS,
  GRAPH_QUERY_BASE,
  graphData,
  keepGraphPreviousData,
  useGraphViewer,
} from './request'
import type { GraphHookOptions, GraphViewer } from './request'

const BROWSERS_PATH = '/graph/v1/browsers'

function browserPath(slug: string): string {
  return `/graph/v1/browsers/${encodeURIComponent(slug)}`
}

export function graphBrowsersOptions(viewer: GraphViewer, filters: GraphBrowsersParams = {}) {
  const normalized = normalizeBrowsersParams(filters ?? {})
  const queryKey: QueryKey = ['graph', 'browsers', 'list', viewer, normalized]
  return queryOptions({
    ...GRAPH_QUERY_BASE,
    queryKey,
    queryFn: () => graphData<GraphBrowsersResponse>(BROWSERS_PATH, browsersWire(normalized)),
    staleTime: GRAPH_DATA_STALE_MS,
    enabled: true,
    meta: { graphViewer: viewer },
  })
}

export function useGraphBrowsers(
  filters: GraphBrowsersParams = {},
  options: GraphHookOptions<GraphBrowsersResponse> = {},
) {
  const viewer = useGraphViewer()
  const base = graphBrowsersOptions(viewer, filters)
  const { keepPrevious, retry, enabled } = options
  return useQuery({
    ...base,
    retry: retry ?? base.retry,
    enabled: base.enabled && (enabled ?? true),
    placeholderData: keepPrevious ? keepGraphPreviousData<GraphBrowsersResponse>(viewer) : undefined,
  })
}

export function graphBrowserOptions(viewer: GraphViewer, slug: string) {
  const queryKey: QueryKey = ['graph', 'browsers', 'detail', slug, viewer]
  return queryOptions({
    ...GRAPH_QUERY_BASE,
    queryKey,
    queryFn: () => graphData<GraphBrowserResponse>(browserPath(slug)),
    staleTime: GRAPH_DATA_STALE_MS,
    enabled: isGraphPathSegment(slug),
    meta: { graphViewer: viewer },
  })
}

export function useGraphBrowser(slug: string, options: GraphHookOptions<GraphBrowserResponse> = {}) {
  const viewer = useGraphViewer()
  const base = graphBrowserOptions(viewer, slug)
  const { keepPrevious, retry, enabled } = options
  return useQuery({
    ...base,
    retry: retry ?? base.retry,
    enabled: base.enabled && (enabled ?? true),
    placeholderData: keepPrevious ? keepGraphPreviousData<GraphBrowserResponse>(viewer) : undefined,
  })
}
