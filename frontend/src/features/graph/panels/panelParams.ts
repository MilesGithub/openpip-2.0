import { useEffect, useState } from 'react'
import { normalizeRelationshipParams } from '../../../api/graph/params'
import type { GraphEvidenceParams, GraphRelationshipParams, GraphSelection } from '../../../api/graph/params'
import type {
  GraphEdge, GraphFiltersEcho, GraphNetworkQuery, GraphNode, GraphOutcome, GraphRelationshipResponse, GraphSelectionEcho,
} from '../types'
import { EMPTY_GRAPH, requestedOutcomes } from '../network/graph'
import type { TypedGraph } from '../network/graph'
import { arrayOf } from './text'

/** One value P1 and P8 may tune (gaps 6). */
export const PANEL_DEBOUNCE_MS = 300

/**
 * The update runs in the timer's callback, since a state set in an effect's body fails lint. A
 * remount starts a new timer, so three activations inside the debounce send one request.
 */
export function usePanelDelay(ms = PANEL_DEBOUNCE_MS): boolean {
  const [ready, setReady] = useState(false)
  useEffect(() => {
    const timer = setTimeout(() => setReady(true), ms)
    return () => clearTimeout(timer)
  }, [ms])
  return ready
}

/** Never both: `browser` with `releases` is a 400. */
export function selectionOf(echo: GraphSelectionEcho | null | undefined): GraphSelection {
  if (echo === null || echo === undefined) return {}
  if (typeof echo.browser === 'string' && echo.browser !== '') return { browser: echo.browser }
  if (Array.isArray(echo.releases)) return { releases: [...echo.releases] }
  return {}
}

/**
 * Each key is read by name, never by spreading the echo: a network-only key, such as `seeds` or
 * `max_edges`, is a 400 on the relationship routes. An explicit `['supported']` stays a list, since
 * the query key and MSW tell it apart from an absent outcome.
 */
export function relationshipParamsOf(echo: (GraphFiltersEcho & GraphSelectionEcho) | null | undefined): GraphRelationshipParams {
  if (echo === null || echo === undefined) return {}
  const params: GraphRelationshipParams = { ...selectionOf(echo) }
  const outcome = echo.outcome
  if (Array.isArray(outcome)) {
    params.outcome = outcome.includes('all') ? 'all' : outcome.filter((value): value is GraphOutcome => value !== 'all')
  }
  if (Array.isArray(echo.evidence_basis)) params.evidence_basis = [...echo.evidence_basis]
  if (Array.isArray(echo.effect)) params.effect = [...echo.effect]
  if (Array.isArray(echo.method)) params.method = [...echo.method]
  if (Array.isArray(echo.level)) params.level = [...echo.level]
  if (Array.isArray(echo.score)) params.score = [...echo.score]
  const context = echo.context
  if (typeof context === 'object' && context !== null && Object.keys(context).length > 0) params.context = { ...context }
  return params
}

/**
 * A reference link an expanded network joined is summarised under the default filter, so its panel takes the network's
 * releases and no filter, as U33's panels do (X1, Q36). With expand, layers name no reference type (Q17), so every
 * reference link of such an answer is a join; null for any other edge or echo.
 */
export function joinParamsOf(edge: GraphEdge | null | undefined, echo: GraphNetworkQuery | null | undefined): GraphRelationshipParams | null {
  if (edge === null || edge === undefined || echo === null || echo === undefined) return null
  return echo.expand === 'reference' && edge.family === 'reference' ? selectionOf(echo) : null
}

/**
 * The list takes every filter but `outcome`, so the results against a relationship are read beside
 * the results for it. A key is deleted from a copy, since a destructured name left unused fails
 * lint. No `page_size`: it is part of the query key and MSW's, and every recording was made without it.
 */
export function evidenceParamsOf(params: GraphRelationshipParams): GraphEvidenceParams {
  const copy: GraphEvidenceParams = { ...params }
  delete copy.outcome
  return copy
}

/** For an entity opened with a panel's params, as `selectionOf` picks the selection from an echo. */
export function selectionFromParams(params: GraphRelationshipParams): GraphSelection {
  if (typeof params.browser === 'string' && params.browser !== '') return { browser: params.browser }
  if (Array.isArray(params.releases)) return { releases: [...params.releases] }
  return {}
}

/** Read before the page loads, so the words follow the params actually sent. */
export function requestedOf(params: GraphRelationshipParams): GraphOutcome[] {
  return requestedOutcomes(params.outcome === undefined ? null : params.outcome === 'all' ? ['all'] : params.outcome)
}

/**
 * Equal for params the query key treats alike. `JSON.stringify` drops the `undefined` keys that the
 * key object keeps, so the parsed string equals the key's last element under `toEqual` only.
 */
export function panelParamsKey(params: GraphRelationshipParams): string {
  return JSON.stringify(normalizeRelationshipParams(params))
}

/** Built this way, it gives the network's sentence for the same edge. */
export function graphOfRelationship(page: GraphRelationshipResponse): TypedGraph {
  return {
    ...EMPTY_GRAPH,
    nodes: arrayOf(page.nodes),
    edges: [page.edge],
    releases: arrayOf(page.releases),
    requestedOutcomes: requestedOutcomes(page.query?.outcome ?? null),
  }
}

/**
 * The kept graph's own record wins, so an end the network does not hold is named as the button
 * that opened the panel named it, before and after its page loads.
 */
export function graphWithNodes(graph: TypedGraph, nodes: readonly GraphNode[] | undefined): TypedGraph {
  if (nodes === undefined) return graph
  const ids = new Set(graph.nodes.map((node) => node.id))
  // The nodes come from another panel's body, which passed only its own guard, and this runs above every boundary.
  const added = nodes.filter((node) => typeof node === 'object' && node !== null && typeof node.id === 'string' && !ids.has(node.id))
  return added.length === 0 ? graph : { ...graph, nodes: [...graph.nodes, ...added] }
}
