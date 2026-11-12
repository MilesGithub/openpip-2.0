import { classifyGraphError } from '../../../api/graph/errors'
import type {
  GraphEdge, GraphEffectKey, GraphOutcome, GraphPresentation, GraphRelationshipTypeEntry, GraphRelease,
  GraphVocabularies,
} from '../types'
import { edgeLine, isNonSupport, nonSupportOutcome } from './channels'
import type { NonSupportOutcome } from './channels'
import { GRAPH_OUTCOMES, own } from './graph'
import type { TypedGraph } from './graph'
import type { NetworkViewState } from './networkView'
import { nodeLabel } from './presentation'

// A deploy can bring a value before this code knows it. Such a value is worded from the
// vocabularies, then from its slug, so nothing prints "undefined" or a prototype member's source.

function slugWords(slug: string): string { return slug.split('_').join(' ') }

/** Architecture decision D20's four strings, verbatim. */
export const LEVEL_WORDS: Readonly<Record<string, string>> = {
  association: 'association (co-purification)',
  physical_association: 'physical association',
  direct_interaction: 'direct interaction',
  not_stated: 'physical interaction, level not stated',
}
/** OPEN_QUESTIONS B14's words without its "(AP-MS)": no edge names the assay its level was assigned from (Y18). */
export const LEVEL_ASSIGNED_WORDS = 'co-complex association, assigned by openPIP from the stated assay'
export const OUTCOME_LINE_WORDS: Readonly<Record<NonSupportOutcome, string>> = {
  tested_absent: 'tested, not detected',
  not_significant: 'not significant at the stated threshold',
  inconclusive: 'inconclusive',
}
export const SIGN_WORDS: Readonly<Record<string, string>> = {
  negative: 'negative (aggravating)',
  positive: 'positive (alleviating)',
  mixed: 'mixed sign',
}
export const EFFECT_WORDS: Readonly<Record<string, string>> = {
  activating: 'activates',
  inhibiting: 'inhibits',
  mixed: 'mixed effect',
  unknown: 'effect unknown',
  not_applicable: 'direction only: this type states no effect',
}

type StatedEffect = 'activating' | 'inhibiting' | 'mixed' | 'unknown'

/**
 * Whole phrases, so each count's verb agrees with it. A result counted as not stated leaves
 * the effect unsaid, which is not a reported absence of effect.
 */
const EFFECT_COUNT_WORDS: readonly (readonly [GraphEffectKey, string, string])[] = [
  ['activating', '1 states activation', '{n} state activation'],
  ['inhibiting', '1 states inhibition', '{n} state inhibition'],
  ['unknown', '1 states an unknown effect', '{n} state an unknown effect'],
  ['not_stated', '1 does not state an effect', '{n} do not state an effect'],
]
const ALSO_WORDS: Readonly<Record<GraphOutcome, readonly [string, string]>> = {
  supported: [
    'also 1 supporting result, not among the requested outcomes',
    'also {n} supporting results, not among the requested outcomes',
  ],
  tested_absent: ['also 1 result tested and not detected', 'also {n} results tested and not detected'],
  inconclusive: ['also 1 inconclusive result', 'also {n} inconclusive results'],
  not_significant: [
    'also 1 result not significant at the stated threshold',
    'also {n} results not significant at the stated threshold',
  ],
}

function statedEffect(effect: GraphEdge['effect']): StatedEffect | null {
  return effect === 'activating' || effect === 'inhibiting' || effect === 'mixed' || effect === 'unknown' ? effect : null
}

/** An empty or missing label is no label. A vocabularies fragment may lack lists, as a test's does. */
function usable(label: string | undefined): string | undefined {
  return typeof label === 'string' && label !== '' ? label : undefined
}

function relationshipType(slug: string, vocabularies?: GraphVocabularies | null): GraphRelationshipTypeEntry | undefined {
  return vocabularies?.relationship_types?.find((entry) => entry.slug === slug)
}

export function levelWords(level: string, vocabularies?: GraphVocabularies | null): string {
  return (
    own(LEVEL_WORDS, level)
    ?? usable(vocabularies?.interaction_levels?.find((entry) => entry.value === level)?.label)
    ?? slugWords(level)
  )
}

export function typeLabel(slug: string, vocabularies?: GraphVocabularies | null): string {
  return usable(relationshipType(slug, vocabularies)?.label) ?? slugWords(slug)
}

export function roleLabels(slug: string, vocabularies?: GraphVocabularies | null): { subject: string; object: string } | null {
  const entry = relationshipType(slug, vocabularies)
  const subject = usable(entry?.subject_role_label)
  const object = usable(entry?.object_role_label)
  return subject !== undefined && object !== undefined ? { subject, object } : null
}

export function entityTypeLabel(slug: string, vocabularies?: GraphVocabularies | null): string {
  return usable(vocabularies?.entity_types?.find((entry) => entry.slug === slug)?.label) ?? slugWords(slug)
}

export function releaseWords(release: GraphRelease): string {
  return `${release.dataset.title} ${release.version}${release.superseded ? ' (superseded)' : ''}`
}

export function edgeReleases(edge: GraphEdge, graph: TypedGraph): GraphRelease[] {
  const found: GraphRelease[] = []
  for (const ref of edge.releases) {
    const release = graph.releases.find((candidate) => candidate.id === ref)
    if (release !== undefined) found.push(release)
  }
  return found
}

/**
 * Whether each release a link lists states it. A request that names another outcome beside
 * `supported` also lists a release whose only results in view do not support the link, while
 * `distinct.releases` counts only the releases that do. The supporting ones are among those
 * listed, so equal counts mean the same releases; otherwise the edge does not say which listed
 * release states it.
 */
export function statedByEachRelease(edge: GraphEdge): boolean {
  return !isNonSupport(edge) && edge.distinct.releases === edge.releases.length
}

/** Empty unless each release it lists states it, so that no release is named as stating a link it does not support. */
export function statingReleases(edge: GraphEdge, graph: TypedGraph): GraphRelease[] {
  return statedByEachRelease(edge) ? edgeReleases(edge, graph) : []
}

/** Callers pass whole clauses, verb included, so the verb agrees with the count. */
export function plural(n: number, one: string, many: string): string {
  return n === 1 ? one : many.split('{n}').join(String(n))
}

export function edgeLabel(edge: GraphEdge, graph: TypedGraph, vocabularies?: GraphVocabularies | null): string {
  // The outcome comes before the reference family, as in the marker and line tables, and a
  // link names its releases only when each one states it: a reference pair without covered
  // support, or one listed with a release whose results in view do not support it, never names
  // a release as stating it.
  const nonSupport = isNonSupport(edge)
  const outcome = nonSupport ? nonSupportOutcome(edge, graph.requestedOutcomes) : null
  if (outcome !== null) {
    const words = own(OUTCOME_LINE_WORDS, outcome) ?? slugWords(outcome)
    const levels = edge.tested_levels[outcome]
    return Array.isArray(levels) && levels.length > 0
      ? `${words}; tested for: ${levels.map((level) => levelWords(level, vocabularies)).join(', ')}`
      : words
  }
  if (edge.family === 'reference' && !nonSupport) {
    const type = typeLabel(edge.type, vocabularies)
    const releases = statingReleases(edge, graph)
    return releases.length > 0 ? `${type} · ${releases.map(releaseWords).join(', ')}` : type
  }
  if (edge.type === 'protein_interaction_unspecified') return typeLabel(edge.type, vocabularies)
  if (edge.family === 'physical' && edge.level !== null) {
    return edge.level_assigned === true ? LEVEL_ASSIGNED_WORDS : levelWords(edge.level, vocabularies)
  }
  if (edge.orientation === 'directed' && edge.effect === 'inhibiting') return EFFECT_WORDS.inhibiting
  const sign = edge.genetic_sign
  if (sign === 'negative' || sign === 'positive' || sign === 'mixed') return own(SIGN_WORDS, sign) ?? slugWords(sign)
  return ''
}

export function edgeDescription(edge: GraphEdge, graph: TypedGraph, presentation?: GraphPresentation | null,
  vocabularies?: GraphVocabularies | null): string {
  const named = (ref: string) => {
    const node = graph.nodes.find((candidate) => candidate.id === ref)
    return node === undefined ? ref : nodeLabel(node, presentation)
  }
  const parts: string[] = []
  const type = typeLabel(edge.type, vocabularies)
  if (edge.orientation === 'symmetric') {
    parts.push(`${type} between ${named(edge.source)} and ${named(edge.target)}`)
  } else {
    const roles = roleLabels(edge.type, vocabularies)
    parts.push(
      roles === null
        ? `${type}: from ${named(edge.source)} to ${named(edge.target)}`
        : `${type}: ${named(edge.source)} (${roles.subject}) to ${named(edge.target)} (${roles.object})`,
    )
  }
  const nonSupport = isNonSupport(edge)
  const effect = statedEffect(edge.effect)
  if (nonSupport) parts.push('no supporting evidence in view')
  else if (effect !== null) parts.push(own(EFFECT_WORDS, effect) ?? slugWords(effect))
  else if (edge.orientation === 'directed' && edge.effect === 'not_applicable') parts.push(EFFECT_WORDS.not_applicable)
  if (edge.orientation === 'directed' && !nonSupport && effect !== null) {
    const counts = EFFECT_COUNT_WORDS
      .filter(([key]) => edge.effect_counts[key] > 0)
      .map(([key, one, many]) => plural(edge.effect_counts[key], one, many))
    if (counts.length > 0) parts.push(`supporting results in view: ${counts.join(', ')}`)
  }
  // The dotted line's meaning, in the legend's words, so the sentence tells a prediction from an
  // experiment as the canvas does.
  if (edgeLine(edge, graph.requestedOutcomes) === 'predicted') {
    parts.push('every supporting result in view is a computational prediction')
  }
  // Part 1 has named the type: a reference link with support adds only the releases that state
  // it, and a label that is the type alone adds nothing.
  const stating = edge.family === 'reference' && !nonSupport
  const releases = stating ? statingReleases(edge, graph) : []
  const label = stating
    ? (releases.length > 0 ? `stated by ${releases.map(releaseWords).join(', ')}` : '')
    : edgeLabel(edge, graph, vocabularies)
  if (label !== '' && label !== type && !parts.includes(label)) parts.push(label)
  // Every count the line does not show is worded, so the sentence says what the canvas cannot.
  const shown = nonSupport ? nonSupportOutcome(edge, graph.requestedOutcomes) : 'supported'
  for (const outcome of GRAPH_OUTCOMES) {
    const n = edge.outcome_counts[outcome]
    const words = own(ALSO_WORDS, outcome)
    if (outcome !== shown && n > 0 && words !== undefined) parts.push(plural(n, words[0], words[1]))
  }
  if (edge.outside_filters > 0) {
    parts.push(plural(
      edge.outside_filters,
      '1 more result outside the current filters',
      '{n} more results outside the current filters',
    ))
  }
  parts.push(plural(edge.distinct.publications, '1 distinct publication', '{n} distinct publications'))
  if (edge.is_synthetic) parts.push('synthetic demonstration data')
  return `${parts.join('; ')}.`
}

export function networkErrorText(error: unknown): string {
  const info = classifyGraphError(error)
  switch (info.kind) {
    case 'too_expensive':
      return 'This network is too large to compute. Narrow the seeds, layers or filters.'
    case 'throttled':
      return info.retryAfter === null
        ? 'Too many requests. Try again shortly.'
        : plural(info.retryAfter, 'Too many requests. Try again in 1 second.', 'Too many requests. Try again in {n} seconds.')
    case 'network':
    case 'timeout':
      return 'The network could not be reached.'
    case 'too_long':
    case 'server_refused':
      return 'The request is too long. Choose fewer entities or filters.'
    default:
      return 'The network could not be loaded.'
  }
}

export function networkStatusText(state: NetworkViewState, options: { error?: unknown; hasGraph: boolean; empty: boolean }): string {
  switch (state) {
    case 'disabled':
      return 'Choose an entity, and a value for each filter in use, to draw its network.'
    case 'loading':
      return 'Loading the network…'
    case 'updating':
      return 'Updating the network…'
    case 'error':
      return options.hasGraph
        ? `${networkErrorText(options.error)} Showing the last answer.`
        : networkErrorText(options.error)
    default:
      return options.empty ? 'No relationships to show.' : ''
  }
}
