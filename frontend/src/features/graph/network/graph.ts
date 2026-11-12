import type {
  GraphEdge, GraphFiltersEcho, GraphNetworkPage, GraphNetworkQuery, GraphNetworkResponse,
  GraphNode, GraphOutcome, GraphRelease, GraphWarning,
} from '../types'

export type TypedGraphNode = GraphNode & { is_seed?: boolean }

export interface TypedGraph {
  nodes: readonly TypedGraphNode[]
  edges: readonly GraphEdge[]
  releases: readonly GraphRelease[]
  /** The outcomes the request asked for, in GRAPH_OUTCOMES order. */
  requestedOutcomes: readonly GraphOutcome[]
  /** The request this graph answers (M28); null for a hand-built graph. */
  query: GraphNetworkQuery | null
  page: GraphNetworkPage | null
  warnings: readonly GraphWarning[]
}

export const GRAPH_OUTCOMES: readonly GraphOutcome[] = ['supported', 'tested_absent', 'inconclusive', 'not_significant']
export const EMPTY_GRAPH: TypedGraph = {
  nodes: [], edges: [], releases: [], requestedOutcomes: ['supported'], query: null, page: null, warnings: [],
}

export type TypedElementKind = 'node' | 'edge'
export interface TypedSelection { kind: TypedElementKind; id: string }
export type TypedSelectionEvent =
  | { kind: 'node'; id: string; record: TypedGraphNode }
  | { kind: 'edge'; id: string; record: GraphEdge }

export type DropReason = 'duplicate-id' | 'missing-endpoint' | 'reserved-id'
export interface DroppedElement { id: string; kind: TypedElementKind; reason: DropReason }
export interface KeptGraph { graph: TypedGraph; dropped: readonly DroppedElement[] }

/** An absent outcome filter is the API's default, which lists supporting evidence only (PAGE:887). */
export function requestedOutcomes(outcome: GraphFiltersEcho['outcome']): GraphOutcome[] {
  if (outcome === null || outcome === undefined) return ['supported']
  if (outcome.includes('all')) return [...GRAPH_OUTCOMES]
  return GRAPH_OUTCOMES.filter((candidate) => outcome.includes(candidate))
}

/** The response's own arrays, uncopied, so a graph costs nothing and callers memoize it on the response. */
export function graphOfNetwork(response: GraphNetworkResponse): TypedGraph {
  return {
    nodes: response.nodes,
    edges: response.edges,
    releases: response.releases,
    requestedOutcomes: requestedOutcomes(response.query.outcome),
    query: response.query,
    page: response.page,
    warnings: response.warnings,
  }
}

function refusedId(id: string, taken: ReadonlySet<string>): DropReason | null {
  if (taken.has(id)) return 'duplicate-id'
  // react-cytoscapejs's diff indexes a plain object, so such an id reads as present and is never added.
  if (id in Object.prototype) return 'reserved-id'
  return null
}

/**
 * Only what Cytoscape can draw, so that the counts, the legend and the notice
 * describe the canvas: nodes and edges share one id space, and an edge whose end
 * is not a kept node would make cy.add throw inside react-cytoscapejs.
 */
export function keptGraph(graph: TypedGraph): KeptGraph {
  const taken = new Set<string>()
  const nodeIds = new Set<string>()
  const nodes: TypedGraphNode[] = []
  const edges: GraphEdge[] = []
  const dropped: DroppedElement[] = []
  for (const node of graph.nodes) {
    const reason = refusedId(node.id, taken)
    if (reason !== null) {
      dropped.push({ id: node.id, kind: 'node', reason })
      continue
    }
    taken.add(node.id)
    nodeIds.add(node.id)
    nodes.push(node)
  }
  for (const edge of graph.edges) {
    const reason = refusedId(edge.id, taken)
      ?? (nodeIds.has(edge.source) && nodeIds.has(edge.target) ? null : 'missing-endpoint')
    if (reason !== null) {
      dropped.push({ id: edge.id, kind: 'edge', reason })
      continue
    }
    taken.add(edge.id)
    edges.push(edge)
  }
  return dropped.length === 0 ? { graph, dropped } : { graph: { ...graph, nodes, edges }, dropped }
}

/** Object.prototype.hasOwnProperty, since Object.hasOwn is past ES2020: a value such as `constructor` never reads a prototype's member. */
export function own<T>(record: Readonly<Record<string, T>>, key: string): T | undefined {
  return Object.prototype.hasOwnProperty.call(record, key) ? record[key] : undefined
}
