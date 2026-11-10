import { queryOptions, useQuery } from '@tanstack/react-query'
import type { QueryKey } from '@tanstack/react-query'
import type { GraphVocabularies } from '../../features/graph/types'
import { GRAPH_QUERY_BASE, graphData } from './request'
import type { GraphCallerFreeHookOptions } from './request'

const VOCABULARIES_PATH = '/graph/v1/vocabularies'

const GRAPH_VOCABULARIES_MS = 60 * 60 * 1000

export function graphVocabulariesOptions() {
  const queryKey: QueryKey = ['graph', 'vocabularies']
  return queryOptions({
    ...GRAPH_QUERY_BASE,
    queryKey,
    queryFn: () => graphData<GraphVocabularies>(VOCABULARIES_PATH),
    staleTime: GRAPH_VOCABULARIES_MS,
    // An unobserved entry is otherwise dropped after 5 minutes.
    gcTime: GRAPH_VOCABULARIES_MS,
  })
}

export function useGraphVocabularies(options: GraphCallerFreeHookOptions<GraphVocabularies> = {}) {
  const base = graphVocabulariesOptions()
  return useQuery({ ...base, ...options, retry: options.retry ?? base.retry })
}
