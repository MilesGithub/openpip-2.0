import type { GraphEdge, GraphOutcome, GraphPresentation, GraphVocabularies } from '../types'
import { derivedPaths, derivedRowSuffix } from '../network/derivedWords'
import { GRAPH_OUTCOMES, own } from '../network/graph'
import type { TypedGraph, TypedGraphNode } from '../network/graph'
import { EFFECT_WORDS, edgeLabel, roleLabels, slugWords } from '../network/words'
import { EDGE_NO_SUPPORT, NOT_REQUESTED, ROW_EFFECT_NULL, endName, outcomeWords, tableStatusWord } from '../panels/panelWords'

export const TABLE_TEXT = {
  caption: 'Relationships in view',
  relationship: 'Relationship',
  first: 'First entity',
  second: 'Second entity',
  type: 'Type',
  direction: 'Direction',
  effect: 'Effect',
  level: 'Level',
  outcome: 'Outcome',
  evidence: 'Independent evidence',
  assertions: 'Supporting results (assertions)',
  publications: 'Reporting publications',
  description: 'Description',
  empty: 'No relationship in view.',
  entities: 'Entities in view',
  seed: ', seed',
  ascending: ' ▲',
  descending: ' ▼',
  selected: '▶ ',
} as const

/**
 * Names in cells are text, never buttons: the "Entities in view" list holds the entities' buttons.
 * `withStatus` is false while the table states one shared status in its sentence.
 */
export function endCell(node: TypedGraphNode | undefined, ref: string, withStatus: boolean,
  presentation?: GraphPresentation | null, vocabularies?: GraphVocabularies | null): string {
  if (node === undefined || node === null) return ref
  const name = endName(node, presentation, vocabularies)
  const word = withStatus ? tableStatusWord(node.status) : null
  return word === null ? name : `${name}, ${word}`
}

/** After a name in "Entities in view": a seed's word, as the canvas's ring says it, or the links a derived seed was reached through (X1, Q32). */
export function entitySuffix(node: TypedGraphNode, graph: TypedGraph, vocabularies?: GraphVocabularies | null): string {
  if (node.is_seed === true) return TABLE_TEXT.seed
  return derivedRowSuffix(derivedPaths(node, graph, vocabularies))
}

export function directionCell(edge: GraphEdge, vocabularies?: GraphVocabularies | null): string {
  const roles = roleLabels(edge.type, vocabularies)
  switch (edge.orientation) {
    case 'symmetric':
      return 'symmetric: the same claim either way'
    case 'directed':
      return roles === null ? 'first to second' : `${roles.subject} to ${roles.object}`
    case 'role_ordered':
      return roles === null
        ? 'first to second, with no claim of cause or effect'
        : `${roles.subject} to ${roles.object}, with no claim of cause or effect`
    default:
      return slugWords(String(edge.orientation))
  }
}

/**
 * The effect's own words, never the canvas label, which is empty for activating, mixed and unknown
 * edges. A null effect with supporting results in view means they were not requested.
 */
export function effectCell(edge: GraphEdge): string {
  const effect = edge.effect
  if (effect === null) return edge.outcome_counts.supported > 0 ? NOT_REQUESTED : EDGE_NO_SUPPORT
  if (effect === 'not_applicable') return edge.orientation === 'directed' ? EFFECT_WORDS.not_applicable : ROW_EFFECT_NULL.noPolarity
  return own(EFFECT_WORDS, effect) ?? slugWords(effect)
}

/** The canvas's own label, for the physical family only. */
export function levelCell(edge: GraphEdge, graph: TypedGraph, vocabularies?: GraphVocabularies | null): string {
  return edge.family === 'physical' ? edgeLabel(edge, graph, vocabularies) : ''
}

export function outcomeCell(edge: GraphEdge): string {
  return GRAPH_OUTCOMES
    .filter((outcome) => edge.outcome_counts[outcome] > 0)
    .map((outcome) => `${edge.outcome_counts[outcome]} ${outcomeWords(outcome)}`)
    .join('; ')
}

/**
 * The independent evidence, assertion and publication counts cover only the supporting results
 * among the requested outcomes, so under a request without them each reads as not requested.
 */
export function countCell(count: number, requested: readonly GraphOutcome[]): string {
  return requested.includes('supported') ? String(count) : NOT_REQUESTED
}
