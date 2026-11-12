import type { GraphEdge } from '../types'
import { isNonSupport } from './channels'
import type { TypedGraph, TypedGraphNode } from './graph'

/** A request for any outcome but `supported` is drawn as asked (Y32); the canvas, the legend and the notice all take this default. */
export function defaultShowNonSupporting(graph: TypedGraph | null): boolean {
  return graph !== null && graph !== undefined && graph.requestedOutcomes.some((outcome) => outcome !== 'supported')
}

export interface VisibleGraph {
  nodes: TypedGraphNode[]; edges: GraphEdge[]; hiddenNodes: TypedGraphNode[]; hiddenEdges: GraphEdge[]
}

/**
 * Takes a kept graph. Hidden elements keep their place in the layout, so hiding never moves
 * what stays; a seed is never hidden, and neither is a node with no edge at all.
 */
export function visibleGraph(graph: TypedGraph, options: { showNonSupporting: boolean }): VisibleGraph {
  if (options.showNonSupporting) {
    return { nodes: [...graph.nodes], edges: [...graph.edges], hiddenNodes: [], hiddenEdges: [] }
  }
  const edges: GraphEdge[] = []
  const hiddenEdges: GraphEdge[] = []
  const touched = new Set<string>()
  const reached = new Set<string>()
  for (const edge of graph.edges) {
    touched.add(edge.source)
    touched.add(edge.target)
    if (isNonSupport(edge)) {
      hiddenEdges.push(edge)
    } else {
      edges.push(edge)
      reached.add(edge.source)
      reached.add(edge.target)
    }
  }
  const nodes: TypedGraphNode[] = []
  const hiddenNodes: TypedGraphNode[] = []
  for (const node of graph.nodes) {
    if (node.is_seed !== true && touched.has(node.id) && !reached.has(node.id)) hiddenNodes.push(node)
    else nodes.push(node)
  }
  return { nodes, edges, hiddenNodes, hiddenEdges }
}
