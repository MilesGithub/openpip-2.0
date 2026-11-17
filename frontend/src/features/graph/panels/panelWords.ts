import { classifyGraphError } from '../../../api/graph/errors'
import type { GraphErrorInfo } from '../../../api/graph/errors'
import type {
  GraphEdge, GraphEntityRef, GraphNode, GraphOutcome, GraphPresentation, GraphRelationshipRef, GraphRelease,
  GraphReleaseCounts, GraphSignKey, GraphTermRef, GraphVocabularies,
} from '../types'
import { isNonSupport, nonSupportOutcome } from '../network/channels'
import { GRAPH_OUTCOMES, own } from '../network/graph'
import { nodeLabel } from '../network/presentation'
import {
  EFFECT_COUNT_WORDS, EFFECT_WORDS, LEVEL_ASSIGNED_WORDS, OUTCOME_LINE_WORDS, SIGN_WORDS, entityTypeLabel, levelWords, plural,
  slugWords, typeLabel,
} from '../network/words'
import { clipText } from './text'

// Constants in a strict module rather than a text group, so the summary and row builders stay pure
// functions. A value a deploy brings early is worded from the vocabularies, then from its slug, so
// nothing prints "undefined" or a prototype member.

export type PanelSubject = 'relationship' | 'evidence' | 'entity'

interface SubjectWords {
  object: string
  subject: string
  request: string
  fewer: string
  notFound: string
  throttled: string
  tooExpensive: string
}

// No word prints a response's `detail`: a 503's names network advice a relationship cannot follow. A
// 404 never says the element does not exist: an absent element, one hidden from you and a switched-off
// route answer alike. The entity route answers no 503 of its own, so its words claim no computation.
const SUBJECT_WORDS: Readonly<Record<PanelSubject, SubjectWords>> = {
  relationship: {
    object: 'this relationship',
    subject: 'This relationship',
    request: 'for this relationship',
    fewer: 'Use fewer filters.',
    notFound: 'This relationship is not available to you.',
    throttled: "Too many requests: relationships share the network's limit.",
    tooExpensive: 'This relationship took too long to compute. Narrow the filters before trying again.',
  },
  evidence: {
    object: 'the evidence',
    subject: 'The evidence',
    request: 'for this evidence',
    fewer: 'Use fewer filters.',
    notFound: "This relationship's evidence is not available to you.",
    throttled: 'Too many requests.',
    tooExpensive: 'This page of evidence took too long to compute. Narrow the filters before trying again.',
  },
  entity: {
    object: 'this entity',
    subject: 'This entity',
    request: 'for this entity',
    fewer: 'Select fewer releases.',
    notFound: 'This entity is not available to you.',
    throttled: 'Too many requests.',
    tooExpensive: 'This entity could not be loaded now. Try again later.',
  },
}

export function panelErrorText(info: GraphErrorInfo, subject: PanelSubject): string {
  const words = SUBJECT_WORDS[subject]
  switch (info.kind) {
    case 'cancelled':
      return `Loading ${words.object} was cancelled.`
    case 'too_long':
      return `The request ${words.request} would be too long to send. ${words.fewer}`
    case 'timeout':
      return `${words.subject} did not arrive in time.`
    case 'network':
      return 'The server could not be reached.'
    case 'bad_request':
      return `The server refused the request ${words.request}.`
    case 'server_refused':
      return `The web server refused the request ${words.request}, which may be too long. ${words.fewer}`
    case 'unauthorized':
      return `Your login is no longer valid, so ${words.object} could not be loaded. Log in again.`
    case 'not_found':
      return words.notFound
    case 'not_acceptable':
      return 'The server cannot answer in a format this page reads.'
    case 'throttled':
      return typeof info.retryAfter === 'number'
        ? plural(info.retryAfter, `${words.throttled} Try again in 1 second.`, `${words.throttled} Try again in {n} seconds.`)
        : `${words.throttled} Try again shortly.`
    case 'too_expensive':
      return words.tooExpensive
    case 'bad_body':
      return "The server's answer could not be read."
    case 'http':
      return `${words.subject} could not be loaded (HTTP ${info.status}).`
    default:
      return `${words.subject} could not be loaded.`
  }
}

export function panelErrorFor(error: unknown, subject: PanelSubject): string {
  return panelErrorText(classifyGraphError(error), subject)
}

/**
 * A repeat cannot change a refused or too long request; after a 401 the interceptor has logged out
 * and the hook's key moves to the visitor's, which sends the request again by itself; and a 404's
 * repeat at the user's hand spends the allowance and reads alike.
 */
export function panelMayRetry(info: GraphErrorInfo): boolean {
  switch (info.kind) {
    case 'too_long':
    case 'bad_request':
    case 'server_refused':
    case 'not_acceptable':
    case 'unauthorized':
    case 'not_found':
      return false
    default:
      return true
  }
}

export const PANEL_LOADING = {
  relationship: 'Loading this relationship…',
  evidence: 'Loading the evidence…',
  evidenceMore: 'Loading more evidence…',
  entity: 'Loading this entity…',
} as const
export const PANEL_WAITING = 'This panel waits for the network.'
export const PANEL_LABELS = {
  close: 'Close',
  tryAgain: {
    relationship: 'Try loading this relationship again',
    evidence: 'Try loading the evidence again',
    entity: 'Try loading this entity again',
  },
  showMore: 'Show more',
  rawToggle: "Source record's columns",
  summary: 'Summary',
  counts: 'Counts in view',
  evidence: 'Evidence (every outcome)',
  releases: 'Releases',
  identifiers: 'Identifiers',
  referenceRelationships: 'Reference relationships',
  relationshipCounts: 'Relationships by type',
  ends: 'Open an end in its own panel:',
  contradiction: 'Contradicting results in view',
  countingLimits: 'The count has these stated limits:',
} as const
export const EVIDENCE_EVERY_OUTCOME =
  'The list shows every outcome, so the results against this relationship are read beside the results for it.'
export const EVIDENCE_EMPTY = 'No result of this relationship passes the current filters.'

/** A row of an outcome a deploy brings early is listed, so the total can fall below the rows: the line never reads "6 of 5". */
export function shownCount(shown: number, total: number | null): string {
  if (total === null || total < shown) return plural(shown, '1 result shown.', '{n} results shown.')
  return `${shown} of ${plural(total, '1 result', '{n} results')} shown.`
}

/** Assertion counts: no independent count per release exists. */
export function byReleaseLine(releaseText: string, results: number): string {
  return `${releaseText}: ${plural(results, '1 result (assertion)', '{n} results (assertions)')}`
}

/** Labels are not unique, so the type tells "SYN-SHARED (Gene)" from "SYN-SHARED (Protein (UniProtKB entry))". */
export function endName(node: GraphNode, presentation?: GraphPresentation | null, vocabularies?: GraphVocabularies | null): string {
  return `${clipText(nodeLabel(node, presentation))} (${entityTypeLabel(node.type, vocabularies)})`
}

/** Each end by its ref, never by `nodes`' order, which is the page's id order. */
export function relationshipName(edge: GraphEdge, nodes: readonly GraphNode[], presentation?: GraphPresentation | null,
  vocabularies?: GraphVocabularies | null): string {
  const named = (ref: string) => {
    const node = nodes.find((candidate) => candidate.id === ref)
    return node === undefined ? ref : endName(node, presentation, vocabularies)
  }
  const type = typeLabel(edge.type, vocabularies)
  return edge.orientation === 'symmetric'
    ? `${type} between ${named(edge.source)} and ${named(edge.target)}`
    : `${type}: ${named(edge.source)} to ${named(edge.target)}`
}

/** Twin edges' panels are named apart as their rows are, so the panel a row opens is named as the row. */
export function relationshipHeading(ref: GraphRelationshipRef, edge: GraphEdge | null, nodes: readonly GraphNode[],
  edges: readonly GraphEdge[], presentation?: GraphPresentation | null, vocabularies?: GraphVocabularies | null): string {
  if (edge === null) return `Relationship ${ref}`
  const name = relationshipName(edge, nodes, presentation, vocabularies)
  const twin = edges.some((other) => other.id !== ref && relationshipName(other, nodes, presentation, vocabularies) === name)
  return twin ? `${name} (${ref})` : name
}

export function entityHeading(ref: GraphEntityRef, node: GraphNode | null, presentation?: GraphPresentation | null,
  vocabularies?: GraphVocabularies | null): string {
  return node === null ? `Entity ${ref}` : endName(node, presentation, vocabularies)
}

export function mergedText(asked: GraphEntityRef): string {
  return `The entity asked for, ${asked}, was merged into this one.`
}

/**
 * No two buttons of one list share a name. Each name has been clipped already, so the suffix tells
 * apart two names that differ only beyond the clip.
 */
export function uniqueNames(items: readonly { id: string; name: string }[]): string[] {
  const counts = new Map<string, number>()
  for (const item of items) counts.set(item.name, (counts.get(item.name) ?? 0) + 1)
  return items.map((item) => ((counts.get(item.name) ?? 0) > 1 ? `${item.name} (${item.id})` : item.name))
}

// milestone M1 adds observation_key to its first sentence; V1 rewrites "match or overlap, or either names none"
export const COUNTING_DEFINITION: string =
  'Independent evidence counts observations, not records. Two results describe one observation, and count once, when they have the same outcome and the same origin (the paper that reports them or, when no paper is named as reporting them, the dataset of their release) and their detection methods match or overlap, or either names none; or when they have the same outcome and the source groups them as one observation in one release. Results linked through others count once with them. A record that names two papers as reporting it gives one result per reporting paper, each with that paper as its origin; a paper it only cites, or names for a method, adds no result. A result that a later release of the same dataset restates with the same outcome, the same reporting paper (or none in either release), and matching detection methods counts once, even when the two state different effects.'
// milestone M1 rewrites the third; V1 retires the second (gaps 6; M31)
export const COUNTING_LIMITS: readonly string[] = [
  'Two experiments with the same outcome, reported in one paper, or stated by one dataset when no paper reports them, count once when their records share a detection method or one names none, whatever evidence basis each states.',
  'One experiment whose method two sources name at different levels of detail in PSI-MI counts twice.',
  'openPIP does not yet read the identifiers that sources share for one observation (an IMEx interaction id, for example); once it does, a record that carries one and a record of the same experiment that does not will count twice.',
]

export const SUPPORTING_LABEL = 'Supporting results (assertions)'
export const INDEPENDENT_LABEL = 'Independent evidence'
export const NOT_REQUESTED = 'not among the requested outcomes'
export const NOT_LISTED =
  'Under the current filters a network would not list this relationship: none of its results in view has a requested outcome.'
export const BY_OUTCOME = { caption: 'Results in view by outcome', outcome: 'Outcome', results: 'Results (assertions)', independent: 'Independent evidence' } as const
export const OUTCOME_WORDS: Readonly<Record<string, string>> = {
  supported: 'supporting',
  tested_absent: OUTCOME_LINE_WORDS.tested_absent,
  inconclusive: OUTCOME_LINE_WORDS.inconclusive,
  not_significant: OUTCOME_LINE_WORDS.not_significant,
}
/** The network sentence's phrases without their "also", which reads wrongly after the line's colon. */
export const NEITHER_WORDS: Readonly<Record<string, readonly [string, string]>> = {
  inconclusive: ['1 inconclusive result', '{n} inconclusive results'],
  not_significant: ['1 result not significant at the stated threshold', '{n} results not significant at the stated threshold'],
}

export function outcomeWords(outcome: string): string {
  return own(OUTCOME_WORDS, outcome) ?? slugWords(outcome)
}

export function outsideLine(edge: GraphEdge): string {
  const n = edge.outside_filters
  const head = `Results outside the current filters: ${n}`
  if (!(n > 0)) return head
  const list = GRAPH_OUTCOMES
    .filter((outcome) => edge.outside_outcome_counts[outcome] > 0)
    .map((outcome) => `${edge.outside_outcome_counts[outcome]} ${outcomeWords(outcome)}`)
  return list.length === 0 ? head : `${head}, by outcome: ${list.join(', ')}`
}

/** It counts reporting papers only, and the supporting results may still cite papers, or name them for a method. */
export function publicationsLine(edge: GraphEdge, requested: readonly GraphOutcome[]): string {
  const head = 'Publications reporting the supporting results'
  if (!requested.includes('supported')) return `${head}: ${NOT_REQUESTED}`
  const n = edge.distinct.publications
  return n === 0 ? `${head}: none named as reporting them` : `${head}: ${n}`
}

/** The outcome an edge without support is listed for is already its own line, so it is left out here. */
export function neitherLine(edge: GraphEdge, requested: readonly GraphOutcome[]): string | null {
  const listedFor = isNonSupport(edge) ? nonSupportOutcome(edge, requested) : null
  const phrases: string[] = []
  for (const outcome of ['inconclusive', 'not_significant'] as const) {
    const n = edge.outcome_counts[outcome]
    const words = own(NEITHER_WORDS, outcome)
    if (n > 0 && outcome !== listedFor && words !== undefined) phrases.push(plural(n, words[0], words[1]))
  }
  return phrases.length === 0 ? null : `Results neither for nor against this relationship: ${phrases.join('; ')}`
}

export const EDGE_NO_SUPPORT = 'no supporting result in view'
export const SUPPORT_NOT_REQUESTED = 'not shown, since supporting results are not among the requested outcomes'
export const SIGN_COUNT_WORDS: readonly (readonly [GraphSignKey, string, string])[] = [
  ['negative', '1 states a negative (aggravating) sign', '{n} state a negative (aggravating) sign'],
  ['positive', '1 states a positive (alleviating) sign', '{n} state a positive (alleviating) sign'],
  ['not_stated', '1 does not state a sign', '{n} do not state a sign'],
]
export const SIGN_MEANING: string =
  'A genetic sign is read relative to the null model of the score that states it; it is not inhibition, and not a result tested and not detected.'
export const UNSPECIFIED_LEVEL = 'none: this type states no interaction type or level and claims no physical contact'

/**
 * A null effect, sign or level means no covered support. Supporting results that pass the filters
 * but were not requested are never called absent.
 */
function missingValue(edge: GraphEdge): string {
  return edge.outcome_counts.supported > 0 ? SUPPORT_NOT_REQUESTED : EDGE_NO_SUPPORT
}

function effectPhrases(edge: GraphEdge): string[] {
  return EFFECT_COUNT_WORDS
    .filter(([key]) => edge.effect_counts[key] > 0)
    .map(([key, one, many]) => plural(edge.effect_counts[key], one, many))
}

function signPhrases(edge: GraphEdge): string[] {
  return SIGN_COUNT_WORDS
    .filter(([key]) => edge.sign_counts[key] > 0)
    .map(([key, one, many]) => plural(edge.sign_counts[key], one, many))
}

/** null when edge.effect is 'not_applicable' */
export function effectLine(edge: GraphEdge): string | null {
  const effect = edge.effect
  if (effect === 'not_applicable') return null
  return `Effect: ${effect === null ? missingValue(edge) : own(EFFECT_WORDS, effect) ?? slugWords(effect)}`
}

export function effectCountsLine(edge: GraphEdge): string | null {
  if (!(edge.assertion_count > 0) || edge.effect === 'not_applicable') return null
  const phrases = effectPhrases(edge)
  return phrases.length === 0 ? null : `Supporting results in view: ${phrases.join(', ')}`
}

/** null when edge.genetic_sign is 'not_applicable' */
export function signLine(edge: GraphEdge): string | null {
  const sign = edge.genetic_sign
  if (sign === 'not_applicable') return null
  const words = sign === null ? missingValue(edge) : sign === 'not_stated' ? 'not stated' : own(SIGN_WORDS, sign) ?? slugWords(sign)
  return `Genetic sign: ${words}`
}

export function signCountsLine(edge: GraphEdge): string | null {
  if (!(edge.assertion_count > 0) || edge.genetic_sign === 'not_applicable') return null
  const phrases = signPhrases(edge)
  return phrases.length === 0 ? null : `Supporting results in view: ${phrases.join(', ')}`
}

/**
 * physical family only. An assigned level was not stated, so its label says "of", not "stated by",
 * and a type that claims no physical contact names no level.
 */
export function levelLine(edge: GraphEdge, vocabularies?: GraphVocabularies | null): string | null {
  if (edge.family !== 'physical') return null
  const label = edge.level_assigned === true
    ? 'Strongest level of the supporting results in view'
    : 'Strongest level stated by the supporting results in view'
  let words: string
  if (edge.type === 'protein_interaction_unspecified') words = UNSPECIFIED_LEVEL
  else if (edge.level === null) words = missingValue(edge)
  else if (edge.level_assigned === true) words = LEVEL_ASSIGNED_WORDS
  else words = levelWords(edge.level, vocabularies)
  return `${label}: ${words}`
}

const STRONGEST_FIRST = ['direct_interaction', 'physical_association', 'association', 'not_stated'] as const

/** Joined by semicolons, since "physical interaction, level not stated" holds a comma. */
export function levelCountsLine(edge: GraphEdge, vocabularies?: GraphVocabularies | null): string | null {
  if (edge.family !== 'physical' || edge.type === 'protein_interaction_unspecified' || edge.level === null) return null
  const list: string[] = []
  for (const level of STRONGEST_FIRST) {
    const n = edge.level_counts[level] ?? 0
    if (n > 0) list.push(`${n} at ${levelWords(level, vocabularies)}`)
  }
  return list.length === 0 ? null : `Supporting results by level: ${list.join('; ')}`
}

/** "Level not stated" without naming the physical type, which a level-not-stated test does not claim. */
function testedLevelWords(level: string, vocabularies?: GraphVocabularies | null): string {
  return level === 'not_stated' ? 'an interaction, level not stated' : levelWords(level, vocabularies)
}

/** A type that claims no physical contact names no level it was tested for: its line is the outcome alone. */
export function testedLevelLines(edge: GraphEdge, vocabularies?: GraphVocabularies | null): string[] {
  if (edge.family !== 'physical') return []
  const lines: string[] = []
  for (const outcome of GRAPH_OUTCOMES) {
    if (outcome === 'supported') continue
    const levels = edge.tested_levels[outcome]
    if (!Array.isArray(levels) || levels.length === 0) continue
    const words = own(OUTCOME_LINE_WORDS, outcome) ?? slugWords(outcome)
    lines.push(edge.type === 'protein_interaction_unspecified'
      ? words
      : `Tested for ${levels.map((level) => testedLevelWords(level, vocabularies)).join('; ')}: ${words}`)
  }
  return lines
}

export const DIFFERENT_RELEASES = 'They come from different releases.'
export const SEVERAL_RELEASES = 'The supporting results come from more than one release.'

/**
 * A release corrects another only by superseding it, and the default view lists current releases,
 * often of different datasets: so a release is named only when a newer release has corrected it.
 * Chosen by count, never through `plural`, whose `{n}` a title could hold.
 */
function correctionClause(entries: readonly GraphReleaseCounts[], releases: readonly GraphRelease[]): string | null {
  const corrected: string[] = []
  for (const entry of entries) {
    const release = releases.find((candidate) => candidate.id === entry.release)
    if (release !== undefined && release.superseded) corrected.push(clipText(`${release.dataset.title} ${release.version}`))
  }
  if (corrected.length === 0) return null
  const list = corrected.join(', ')
  return corrected.length === 1
    ? `${list} is among them, and a newer release has since corrected it.`
    : `${list} are among them, and newer releases have since corrected them.`
}

/** The API gives no effect per release, so the words never say that a release states one effect. */
export function contradictionSentences(edge: GraphEdge, byRelease: readonly GraphReleaseCounts[] | null,
  releases: readonly GraphRelease[], vocabularies?: GraphVocabularies | null): string[] {
  const sentences: string[] = []
  const supported = edge.outcome_counts.supported
  const testedAbsent = edge.outcome_counts.tested_absent
  if (supported > 0 && testedAbsent > 0) {
    const levels = edge.tested_levels.tested_absent
    const tested = edge.family === 'physical' && edge.type !== 'protein_interaction_unspecified'
      && Array.isArray(levels) && levels.length > 0
      ? `${plural(testedAbsent, '1 result', '{n} results')} tested for ${levels.map((level) => testedLevelWords(level, vocabularies)).join(' or ')} and not detected`
      : plural(testedAbsent, '1 result tested and not detected', '{n} results tested and not detected')
    sentences.push(`${plural(supported, '1 supporting result', '{n} supporting results')} and ${tested}.`)
    if (byRelease !== null
      && !byRelease.some((entry) => entry.outcome_counts.supported > 0 && entry.outcome_counts.tested_absent > 0)) {
      sentences.push(DIFFERENT_RELEASES)
      const concerned = byRelease.filter((entry) => entry.outcome_counts.supported > 0 || entry.outcome_counts.tested_absent > 0)
      const clause = correctionClause(concerned, releases)
      if (clause !== null) sentences.push(clause)
    }
  }
  const mixedEffect = edge.effect === 'mixed'
  const mixedSign = edge.genetic_sign === 'mixed'
  if (mixedEffect) sentences.push(`Supporting results state different effects: ${effectPhrases(edge).join(', ')}.`)
  if (mixedSign) sentences.push(`Supporting results state different genetic signs: ${signPhrases(edge).join(', ')}.`)
  if ((mixedEffect || mixedSign) && byRelease !== null) {
    const supporting = byRelease.filter((entry) => entry.outcome_counts.supported > 0)
    if (supporting.length >= 2) {
      sentences.push(SEVERAL_RELEASES)
      const clause = correctionClause(supporting, releases)
      if (clause !== null) sentences.push(clause)
    }
  }
  return sentences
}

export const ROW_OUTCOME_TITLES: Readonly<Record<string, string>> = {
  supported: 'Supporting result',
  tested_absent: 'Tested, not detected',
  inconclusive: 'Inconclusive result',
  not_significant: 'Not significant at the stated threshold',
}
export const OUTSIDE_REQUEST = ' (not among the requested outcomes)'
export const ROW_TERMS: { readonly [K in RowField]: string } = {
  release: 'Release',
  basis: 'Evidence basis',
  method: 'Assertion method',
  effect: 'Effect',
  effectAsStated: 'Effect as stated',
  sign: 'Genetic sign',
  interactionType: 'Interaction type',
  level: 'Interaction level',
  relationshipAsStated: 'Relationship as stated',
  methods: 'Methods',
  eco: 'ECO evidence code',
  scores: 'Scores',
  threshold: 'Significance threshold',
  qualifiers: 'Qualifiers',
  contexts: 'Contexts',
  participants: 'Participants',
  group: 'Evidence group',
  publications: 'Publications',
  record: 'Source record',
}
export type RowField = 'release' | 'basis' | 'method' | 'effect' | 'effectAsStated' | 'sign' | 'interactionType' | 'level'
  | 'relationshipAsStated' | 'methods' | 'eco' | 'scores' | 'threshold' | 'qualifiers' | 'contexts' | 'participants'
  | 'group' | 'publications' | 'record'
export const BASIS_GLOSSES: Readonly<Record<string, string>> = {
  experimental: 'rests on an experiment',
  computational_prediction: 'rests on a computational prediction, not an experiment',
  computational_inference: 'inferred computationally, not an experiment',
  statistical: 'rests on a statistical analysis',
  text_mining: 'found by mining text',
  unknown: 'unknown or not stated',
}
export const METHOD_GLOSSES: Readonly<Record<string, string>> = {
  manual_assertion: 'a person reviewed the evidence and made the statement',
  automatic_assertion: "made by a program without a person's review",
  unknown: 'not stated',
}
/** A row's null effect has three meanings, and each has its own words. */
export const ROW_EFFECT_NULL: { readonly noPolarity: string; readonly notSupporting: string; readonly notStated: string } = {
  noPolarity: 'not applicable: this type states no effect',
  notSupporting: 'none: only a supporting result states an effect',
  notStated: 'not stated by the source',
}
export const ROW_SIGN_NULL: { readonly notSupporting: string; readonly notStated: string } = {
  notSupporting: 'none: only a supporting result states a sign',
  notStated: 'not stated by the source',
}
export const METHOD_ROLE_WORDS: Readonly<Record<string, string>> = {
  detection: 'detection method',
  participant_identification: 'participant identification method',
  evidence_code: 'evidence code',
}
export const RESOLUTION_WORDS: Readonly<Record<string, string>> = {
  primary: 'by its primary identifier',
  xref_unique: 'through a cross-reference that only this entity holds',
  alias_unique: 'through a symbol or alias that only this entity holds',
  isoform_entry: 'on its protein entry: the source named an isoform',
  curated: 'by a reviewed resolution file',
}
export const PUBLICATION_ROLE_WORDS: Readonly<Record<string, string>> = {
  reported_in: 'reported in',
  method_reference: 'method reference',
  cited: 'cited',
}
export const VALUE_KIND_WORDS: Readonly<Record<string, string>> = {
  probability: 'a probability',
  p_value: 'a p-value',
  q_value: 'a q-value',
  rank: 'a rank',
  count: 'a count',
  continuous: 'a continuous value',
}
export const DIRECTION_WORDS: Readonly<Record<string, string>> = {
  higher_is_stronger: 'higher is stronger',
  lower_is_stronger: 'lower is stronger',
  signed: 'signed',
  unordered: 'unordered',
}
export const CENSORED = ' (as written; no exact number)'
/** The type's own statement: nothing in openPIP compares values across datasets by that flag. */
export const NOT_COMPARABLE = '; its type says its values are not comparable across datasets'
export const SCORES_NOTE = 'Each score is read within its own type; scores of different types are never pooled.'
/** A level beside 'Tested, not detected' never reads as a finding. */
export const LEVEL_TESTED_TERM = 'Interaction level tested'
export const NONE_STATED = 'none stated'
export const NO_PUBLICATION = 'no publication recorded'
export const NO_RECORD = 'no stored source record'
/** No reason is given: staff-gating and a release without raw rows read alike. */
export const RAW_UNAVAILABLE = "the source record's columns are not available here"

/** A term labelled by a synthetic release has a null label, so its CURIE stands alone. */
export function termText(term: GraphTermRef): string {
  return typeof term.label === 'string' && term.label !== '' ? `${clipText(term.label)} (${clipText(term.curie)})` : clipText(term.curie)
}

export function contextTermText(term: GraphTermRef): string {
  return typeof term.label === 'string' && term.label !== '' ? clipText(term.label) : clipText(term.curie)
}

export function moreColumnsText(hidden: number): string {
  return plural(hidden, '1 more column not shown.', '{n} more columns not shown.')
}

export const RELEASE_FLAGS = { staged: ' (staged: not yet published)', synthetic: ' (synthetic)' } as const
export const RELEASE_STATUS_WORDS: Readonly<Record<string, string>> = {
  published: 'published',
  superseded: 'superseded: a newer release has since corrected it',
  staged: 'staged: not yet published',
}
export const RELEASE_TERMS = {
  release: 'Release', status: 'Status', scopes: 'Approved for', data: 'Data', licence: 'Licence', attribution: 'Attribution',
  changes: "What openPIP's import changes",
} as const

export const ENTITY_STATUS_WORDS: Readonly<Record<string, string>> = {
  active: 'a reference release states this identifier',
  provisional: "created from a release's evidence; no reference release has stated this identifier yet",
  merged: 'merged into another record',
  retired: 'retired, kept for the record',
}

export function entityStatusSentence(status: string | null): string {
  if (status === null || status === undefined) return 'Status not given.'
  const words = own(ENTITY_STATUS_WORDS, status)
  return words === undefined ? `Status: ${slugWords(status)}.` : `Status: ${status} (${words}).`
}

/** No status is stated, so none is worded: the panel says "Status not given.". */
export function tableStatusWord(status: string | null): string | null {
  if (status === null || status === undefined || status === 'active') return null
  if (status === 'provisional' || status === 'merged' || status === 'retired') return status
  return slugWords(status)
}

/**
 * A network whose entities share one status says it once, not twice on every row, while real data
 * still tell a provisional entity from an active one.
 */
export function tableStatusSentence(statuses: readonly (string | null)[]): string | null {
  if (statuses.length === 0) return null
  const first = statuses[0]
  if (!statuses.every((status) => status === first)) return null
  if (first === null || first === undefined || tableStatusWord(first) === null) return null
  const words = own(ENTITY_STATUS_WORDS, first)
  return words === undefined ? `Every entity in view is ${slugWords(first)}.` : `Every entity in view is ${first}: ${words}.`
}

export const ENTITY_TERMS = {
  type: 'Type', taxon: 'Taxon', createdBy: 'Created by', namespace: 'Namespace', value: 'Value', version: 'Version',
  role: 'Role', link: 'Link',
} as const
/** They count relationships with supporting evidence in the selected releases, never the network's figures. */
export const RELATIONSHIP_COUNTS_NOTE = 'Relationships with supporting evidence in the selected releases, before any evidence filter:'
export const NO_RELATIONSHIP_COUNTS = 'No relationship with supporting evidence in the selected releases.'
export const NO_REFERENCE_RELATIONSHIPS = 'No reference relationship with supporting evidence in the selected releases.'
