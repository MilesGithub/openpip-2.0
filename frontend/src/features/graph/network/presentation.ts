import type { GraphNetworkParams } from '../../../api/graph/params'
import type {
  GraphDirection, GraphEffectKey, GraphEvidenceBasis, GraphLevel, GraphNode, GraphOutcome, GraphPresentation,
} from '../types'
import { GRAPH_OUTCOMES, own } from './graph'

// The lists of backend/browsers/presentation.py, in its order. Its test
// test_presentation_frontend.py reads the lines between the two markers, so each
// list stays on one line, and a change to either side fails until both agree.
// presentation-lists:start
export const PRESENTATION_KEYS = ['version', 'default_layers', 'default_filters', 'filter_panels', 'layout', 'evidence_panels', 'node_label_namespace', 'accent'] as const
export const PRESENTATION_FILTER_PANELS = ['layers', 'direction', 'outcome', 'evidence_basis', 'effect', 'method', 'level', 'score', 'context', 'taxon'] as const
export const PRESENTATION_EVIDENCE_PANELS = ['summary', 'evidence', 'publications', 'scores', 'contexts', 'record'] as const
export const PRESENTATION_LAYOUTS = ['cose', 'cola', 'concentric', 'circle', 'grid', 'breadthfirst'] as const
export const PRESENTATION_ACCENTS = ['blue', 'teal', 'green', 'amber', 'red', 'purple', 'grey'] as const
export const PRESENTATION_DEFAULT_FILTER_NAMES = ['direction', 'outcome', 'evidence_basis', 'effect', 'level'] as const
export const PRESENTATION_DEFAULT_LAYOUT = 'cose'
export const PRESENTATION_DEFAULT_ACCENT = 'blue'
// presentation-lists:end

export type TypedLayoutName = (typeof PRESENTATION_LAYOUTS)[number]

// Records over the unions of types.ts, so that tsc fails when a union gains or loses a member.
const DIRECTIONS: Readonly<Record<GraphDirection, true>> = { out: true, in: true, both: true }
const EVIDENCE_BASES: Readonly<Record<GraphEvidenceBasis, true>> = {
  experimental: true, computational_prediction: true, computational_inference: true,
  statistical: true, text_mining: true, unknown: true,
}
const EFFECT_KEYS: Readonly<Record<GraphEffectKey, true>> = { activating: true, inhibiting: true, unknown: true, not_stated: true }
const LEVELS: Readonly<Record<GraphLevel, true>> = { direct_interaction: true, physical_association: true, association: true, not_stated: true }

export function resolveLayout(name: string | null | undefined): TypedLayoutName {
  return PRESENTATION_LAYOUTS.find((layout) => layout === name) ?? PRESENTATION_DEFAULT_LAYOUT
}

/** Only the primary identifier's namespace can be honoured until the API gives nodes another (Y22). */
export function nodeLabel(node: GraphNode, presentation?: GraphPresentation | null): string {
  const namespace = presentation?.node_label_namespace
  if (typeof namespace === 'string' && namespace !== '' && namespace === node.primary_identifier?.namespace) {
    return node.primary_identifier.value
  }
  return node.label
}

function isIn<T extends string>(known: Readonly<Record<T, true>>, value: unknown): value is T {
  return typeof value === 'string' && own(known, value) === true
}

function isOutcome(value: unknown): value is GraphOutcome {
  return typeof value === 'string' && (GRAPH_OUTCOMES as readonly string[]).includes(value)
}

/** The names of `value` in a closed set, or undefined: an empty list is no filter, while `[]` would disable the hook. */
function closedNames<T extends string>(value: unknown, known: Readonly<Record<T, true>>): T[] | undefined {
  if (!Array.isArray(value)) return undefined
  const names = value.filter((name): name is T => isIn(known, name))
  return names.length > 0 ? names : undefined
}

/**
 * The network params an instance's presentation asks for, each value checked against its closed
 * set; absent means the API's default. `Omit` is not distributive, so the result spreads into
 * either member of GraphSelection: `{ ...presentationParams(instance.presentation), seeds, browser }`.
 */
export function presentationParams(presentation: GraphPresentation | null | undefined): Omit<GraphNetworkParams, 'seeds' | 'releases' | 'browser'> {
  const params: Omit<GraphNetworkParams, 'seeds' | 'releases' | 'browser'> = {}
  if (presentation === null || presentation === undefined) return params
  // A row saved without full_clean() can name '' or null, which normalizeNetworkParams drops, and
  // a list left empty would disable the hook.
  const layers = Array.isArray(presentation.default_layers)
    ? presentation.default_layers.filter((name) => typeof name === 'string' && name !== '')
    : []
  if (layers.length > 0) params.layers = layers
  const filters = presentation.default_filters
  if (typeof filters !== 'object' || filters === null) return params
  const direction = filters.direction
  if (isIn(DIRECTIONS, direction)) params.direction = direction
  const outcome = filters.outcome
  if (Array.isArray(outcome) && outcome.includes('all')) {
    params.outcome = 'all'
  } else if (Array.isArray(outcome)) {
    const outcomes = outcome.filter(isOutcome)
    if (outcomes.length > 0) params.outcome = outcomes
  }
  const evidenceBasis = closedNames(filters.evidence_basis, EVIDENCE_BASES)
  if (evidenceBasis !== undefined) params.evidence_basis = evidenceBasis
  const effects = closedNames(filters.effect, EFFECT_KEYS)
  if (effects !== undefined) params.effect = effects
  const levels = closedNames(filters.level, LEVELS)
  if (levels !== undefined) params.level = levels
  return params
}
