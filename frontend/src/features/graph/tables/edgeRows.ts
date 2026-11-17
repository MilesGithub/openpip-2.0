import type { GraphEdge, GraphEntityRef, GraphPresentation, GraphRelationshipRef, GraphVocabularies } from '../types'
import { EMPTY_GRAPH, keptGraph } from '../network/graph'
import type { TypedGraph, TypedGraphNode } from '../network/graph'
import { nodeLabel } from '../network/presentation'
import { visibleGraph } from '../network/visibility'
import { edgeDescription, typeLabel } from '../network/words'
import { relationshipName, tableStatusSentence, uniqueNames } from '../panels/panelWords'
import { clipText } from '../panels/text'
import { TABLE_TEXT, countCell, directionCell, effectCell, endCell, levelCell, outcomeCell } from './tableWords'

export type EdgeSortKey = 'first' | 'second' | 'type' | 'evidence' | 'assertions' | 'publications'
export interface EdgeSort { key: EdgeSortKey; direction: 'ascending' | 'descending' }
export interface EdgeRow {
  id: GraphRelationshipRef
  edge: GraphEdge
  rank: number                        // position in the API's rank order (TYPES:594-595)
  name: string                        // the row button's text and accessible name, unique in the table (M16)
  first: string                       // the source end: endCell (13.2)
  second: string                      // the target end
  firstName: string                   // sort keys: nodeLabel of each end
  secondName: string
  type: string
  direction: string
  effect: string
  level: string
  outcome: string
  evidence: string
  assertions: string
  publications: string
  evidenceCount: number | null        // null when supporting results are not requested
  assertionCount: number | null
  publicationCount: number | null     // null likewise (M3)
  description: string                 // edgeDescription, through clipText (M36)
}
export interface EntityRow { id: GraphEntityRef; node: TypedGraphNode; name: string }

/**
 * Exactly as the canvas computes its elements, so the rows are the canvas's visible edges in its
 * order. A name is unique over every kept edge, visible or not, which is the panel heading's test,
 * so the panel a row opens is named as the row. While every entity in view shares one status the
 * table states it once, and no name carries a status word.
 */
export function edgeRows(graph: TypedGraph | null, options: {
  showNonSupporting: boolean
  presentation?: GraphPresentation | null
  vocabularies?: GraphVocabularies | null
}): EdgeRow[] {
  const { showNonSupporting, presentation, vocabularies } = options
  const kept = keptGraph(graph ?? EMPTY_GRAPH).graph
  const visible = visibleGraph(kept, { showNonSupporting })
  const withStatus = tableStatusSentence(visible.nodes.map((node) => node.status)) === null
  const requested = kept.requestedOutcomes
  const supported = requested.includes('supported')
  const names = uniqueNames(kept.edges.map((edge) => ({ id: edge.id, name: relationshipName(edge, kept.nodes, presentation, vocabularies) })))
  const nameOf = new Map<string, string>()
  kept.edges.forEach((edge, index) => nameOf.set(edge.id, names[index]))
  const nodeOf = new Map<string, TypedGraphNode>()
  for (const node of kept.nodes) nodeOf.set(node.id, node)
  return visible.edges.map((edge, rank): EdgeRow => {
    const source = nodeOf.get(edge.source)
    const target = nodeOf.get(edge.target)
    return {
      id: edge.id,
      edge,
      rank,
      name: nameOf.get(edge.id) ?? edge.id,
      first: endCell(source, edge.source, withStatus, presentation, vocabularies),
      second: endCell(target, edge.target, withStatus, presentation, vocabularies),
      firstName: source === undefined ? edge.source : nodeLabel(source, presentation),
      secondName: target === undefined ? edge.target : nodeLabel(target, presentation),
      type: typeLabel(edge.type, vocabularies),
      direction: directionCell(edge, vocabularies),
      effect: effectCell(edge),
      level: levelCell(edge, kept, vocabularies),
      outcome: outcomeCell(edge),
      evidence: countCell(edge.evidence_count, requested),
      assertions: countCell(edge.assertion_count, requested),
      publications: countCell(edge.distinct.publications, requested),
      evidenceCount: supported ? edge.evidence_count : null,
      assertionCount: supported ? edge.assertion_count : null,
      publicationCount: supported ? edge.distinct.publications : null,
      description: clipText(edgeDescription(edge, kept, presentation, vocabularies)),
    }
  })
}

function compareText(a: string, b: string): number {
  return a.localeCompare(b, 'en', { numeric: true })
}

function compareCount(a: number | null, b: number | null): number {
  if (a === b) return 0
  if (a === null) return -1
  if (b === null) return 1
  return a - b
}

/**
 * Always a copy: the graph's arrays are readonly. Ties fall back to the id in both directions, so
 * the order is total and stable.
 */
export function sortEdgeRows(rows: readonly EdgeRow[], sort: EdgeSort | null): EdgeRow[] {
  if (sort === null) return [...rows].sort((a, b) => a.rank - b.rank)
  const sign = sort.direction === 'descending' ? -1 : 1
  const compare = (a: EdgeRow, b: EdgeRow): number => {
    switch (sort.key) {
      case 'first':
        return compareText(a.firstName, b.firstName)
      case 'second':
        return compareText(a.secondName, b.secondName)
      case 'type':
        return compareText(a.type, b.type)
      case 'evidence':
        return compareCount(a.evidenceCount, b.evidenceCount)
      case 'assertions':
        return compareCount(a.assertionCount, b.assertionCount)
      default:
        return compareCount(a.publicationCount, b.publicationCount)
    }
  }
  return [...rows].sort((a, b) => sign * compare(a, b) || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0))
}

/** A third activation gives back the API's rank order. */
export function nextSort(current: EdgeSort | null, key: EdgeSortKey): EdgeSort | null {
  if (current === null || current.key !== key) return { key, direction: 'ascending' }
  return current.direction === 'ascending' ? { key, direction: 'descending' } : null
}

/**
 * Every entity in view, so a keyboard user opens any entity panel as a tap on its node does, with
 * one request that spends none of the network's allowance. A seed's name says so in words, as the
 * canvas's two-tone ring does.
 */
export function entityRows(graph: TypedGraph | null, options: {
  showNonSupporting: boolean
  presentation?: GraphPresentation | null
  vocabularies?: GraphVocabularies | null
}): EntityRow[] {
  const { showNonSupporting, presentation, vocabularies } = options
  const kept = keptGraph(graph ?? EMPTY_GRAPH).graph
  const visible = visibleGraph(kept, { showNonSupporting })
  const withStatus = tableStatusSentence(visible.nodes.map((node) => node.status)) === null
  const names = uniqueNames(visible.nodes.map((node) => ({
    id: node.id,
    name: endCell(node, node.id, withStatus, presentation, vocabularies) + (node.is_seed === true ? TABLE_TEXT.seed : ''),
  })))
  return visible.nodes.map((node, index) => ({ id: node.id, node, name: names[index] }))
}
