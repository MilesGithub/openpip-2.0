import type { ElementDefinition } from 'cytoscape'
import type { GraphEdge, GraphPresentation, GraphVocabularies } from '../types'
import { edgeClasses, nodeClasses } from './channels'
import type { TypedGraph, TypedGraphNode } from './graph'
import { nodeLabel } from './presentation'
import { visibleGraph } from './visibility'
import { edgeLabel } from './words'

export interface BuiltElements {
  elements: ElementDefinition[]               // cytoscape's own type; nodes first, then edges
  nodes: ReadonlyMap<string, TypedGraphNode>
  edges: ReadonlyMap<string, GraphEdge>
}
export interface BuildOptions {
  showNonSupporting: boolean
  selectedId?: string | null
  presentation?: GraphPresentation | null
  vocabularies?: GraphVocabularies | null
}

/**
 * Takes a kept graph; ids are the API's refs, never parsed. `data` holds primitives only:
 * react-cytoscapejs compares them key by key, so an equal refetch patches nothing, while a
 * nested record would be patched on every render. The selection is a class, since the
 * component replaces an element's whole class list on each patch. No position is set.
 */
export function buildElements(kept: TypedGraph, options: BuildOptions): BuiltElements {
  const visible = visibleGraph(kept, { showNonSupporting: options.showNonSupporting })
  const hidden = new Set<string>()
  for (const node of visible.hiddenNodes) hidden.add(node.id)
  for (const edge of visible.hiddenEdges) hidden.add(edge.id)
  const selectedId = options.selectedId ?? null
  const classes = (id: string, base: string[]) => {
    const names = [...base]
    if (hidden.has(id)) names.push('hidden')
    if (id === selectedId) names.push('is-selected')
    return names.join(' ')
  }
  const elements: ElementDefinition[] = []
  const nodes = new Map<string, TypedGraphNode>()
  const edges = new Map<string, GraphEdge>()
  for (const node of kept.nodes) {
    nodes.set(node.id, node)
    elements.push({
      data: { id: node.id, label: nodeLabel(node, options.presentation), type: node.type },
      classes: classes(node.id, nodeClasses(node)),
    })
  }
  for (const edge of kept.edges) {
    edges.set(edge.id, edge)
    elements.push({
      data: {
        id: edge.id,
        source: edge.source,
        target: edge.target,
        label: edgeLabel(edge, kept, options.vocabularies),
        publications: edge.distinct.publications,
      },
      classes: classes(edge.id, edgeClasses(edge, kept.requestedOutcomes)),
    })
  }
  return { elements, nodes, edges }
}

/** JSON of the sorted kept ids, hidden ones included (Y35): a ref is opaque and may hold any separator. */
export function layoutKey(kept: TypedGraph, layoutName: string): string {
  const nodeIds = kept.nodes.map((node) => node.id).sort()
  const edgeIds = kept.edges.map((edge) => edge.id).sort()
  return JSON.stringify([layoutName, nodeIds, edgeIds])
}
