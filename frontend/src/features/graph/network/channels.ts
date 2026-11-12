import type { GraphEdge, GraphNode, GraphOutcome } from '../types'
import type { TypedGraphNode } from './graph'

export type EdgeMarker =
  | 'triangle' | 'tee' | 'diamond' | 'vee-hollow' | 'circle'
  | 'square-direction' | 'chevron-reference' | 'none'
export type EdgeLine =
  | 'supported' | 'predicted' | 'tested-absent' | 'not-significant' | 'inconclusive'
  | 'non-support' | 'reference'
export type NonSupportOutcome = 'tested_absent' | 'not_significant' | 'inconclusive'

/** The order in which a line shows a non-supporting outcome when several apply (Y7). */
const NON_SUPPORT_OUTCOMES: readonly NonSupportOutcome[] = ['tested_absent', 'not_significant', 'inconclusive']

/**
 * Listed only for evidence that does not support it. Never `outcome_counts.tested_absent > 0`:
 * an edge with a supporting and a tested-absent result is solid unless only tested-absent
 * results were asked for.
 */
export function isNonSupport(edge: GraphEdge): boolean {
  return edge.assertion_count === 0
}

/** A listed edge without support always has a requested outcome with a count (backend/graph/summary.py); the fallbacks guard hand-built data. */
export function nonSupportOutcome(edge: GraphEdge, requested: readonly GraphOutcome[]): NonSupportOutcome | null {
  const counted = NON_SUPPORT_OUTCOMES.filter((outcome) => edge.outcome_counts[outcome] > 0)
  const asked = counted.find((outcome) => requested.includes(outcome))
  if (asked !== undefined) return asked
  return counted.length > 0 ? counted[0] : null
}

export function isPredicted(edge: GraphEdge): boolean {
  const bases = Object.keys(edge.by_basis)
  return bases.length > 0 && bases.every((basis) => basis === 'computational_prediction')
}

/**
 * No covered support comes first, so a reference pair listed only for a result that does not
 * support it is drawn as that result, never as a link a release states. Family is then tested
 * before orientation, so a reference link never takes the role-ordered circle.
 */
export function edgeMarker(edge: GraphEdge): EdgeMarker {
  if (isNonSupport(edge)) return 'none'
  if (edge.family === 'reference') return 'chevron-reference'
  if (edge.orientation === 'symmetric') return 'none'
  if (edge.orientation === 'role_ordered') return 'circle'
  if (edge.orientation === 'directed') {
    switch (edge.effect) {
      case 'activating':
        return 'triangle'
      case 'inhibiting':
        return 'tee'
      case 'mixed':
        return 'diamond'
      case 'unknown':
        return 'vee-hollow'
      case 'not_applicable':
        return 'square-direction'
      default:
        return 'none'
    }
  }
  return 'none'
}

/** Support is tested before the reference family, as in edgeMarker, so that a reference pair's line and marker agree. */
export function edgeLine(edge: GraphEdge, requested: readonly GraphOutcome[]): EdgeLine {
  if (isNonSupport(edge)) {
    switch (nonSupportOutcome(edge, requested)) {
      case 'tested_absent':
        return 'tested-absent'
      case 'not_significant':
        return 'not-significant'
      case 'inconclusive':
        return 'inconclusive'
      default:
        return 'non-support'
    }
  }
  if (edge.family === 'reference') return 'reference'
  return isPredicted(edge) ? 'predicted' : 'supported'
}

/** The stylesheet's `mapData(publications, 1, 5, 1.5, 5.5)` in numbers; typedStyles.test.ts holds the two equal. */
export function edgeWidthPx(edge: GraphEdge, requested: readonly GraphOutcome[]): number {
  const line = edgeLine(edge, requested)
  if (line === 'supported' || line === 'predicted') {
    const p = edge.distinct.publications
    return Math.min(5.5, Math.max(1.5, 1.5 + (p - 1)))
  }
  return line === 'tested-absent' ? 1.5 : 1
}

/** A slug a deploy brings early cannot break the class list or a selector. */
export function classToken(value: string): string {
  return value.replace(/[^A-Za-z0-9_-]/g, '_')
}

export function edgeClasses(edge: GraphEdge, requested: readonly GraphOutcome[]): string[] {
  const classes = [`rt-${classToken(edge.type)}`, `fam-${classToken(edge.family)}`]
  if (edge.orientation === 'directed') classes.push('directed')
  else if (edge.orientation === 'symmetric') classes.push('symmetric')
  else if (edge.orientation === 'role_ordered') classes.push('role-ordered')
  const effect = edge.effect
  if (effect === 'activating' || effect === 'inhibiting' || effect === 'mixed' || effect === 'unknown') {
    classes.push(`eff-${effect}`)
  }
  const sign = edge.genetic_sign
  if (sign === 'negative' || sign === 'positive' || sign === 'mixed') classes.push(`gsign-${sign}`)
  const line = edgeLine(edge, requested)
  if (line === 'tested-absent' || line === 'not-significant' || line === 'inconclusive') classes.push(`out-${line}`)
  if (line === 'predicted') classes.push('basis-predicted')
  if (edge.is_synthetic) classes.push('synthetic')
  classes.push(`mk-${edgeMarker(edge)}`, `ln-${line}`)
  return classes
}

export function nodeShape(node: GraphNode): 'gene' | 'protein' | 'isoform' | 'other' {
  switch (node.type) {
    case 'gene':
      return 'gene'
    case 'protein':
      return 'protein'
    case 'protein_isoform':
      return 'isoform'
    default:
      return 'other'
  }
}

export function nodeClasses(node: TypedGraphNode): string[] {
  const classes = [`et-${classToken(node.type)}`, `sh-${nodeShape(node)}`]
  if (node.is_seed === true) classes.push('seed')
  return classes
}
