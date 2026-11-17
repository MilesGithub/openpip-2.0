import { isAxiosError } from 'axios'
import type { useGraphEvidence } from '../../../api/graph/relationships'
import type {
  GraphEdge, GraphEvidenceRef, GraphEvidenceRow, GraphNode, GraphOutcome, GraphPresentation, GraphPublication, GraphRelease,
  GraphVocabularies,
} from '../types'
import { own } from '../network/graph'
import { nodeLabel } from '../network/presentation'
import { EFFECT_WORDS, LEVEL_ASSIGNED_WORDS, SIGN_WORDS, levelWords, roleLabels, slugWords } from '../network/words'
import { isEvidencePage } from './guards'
import {
  BASIS_GLOSSES, CENSORED, DIRECTION_WORDS, LEVEL_TESTED_TERM, METHOD_GLOSSES, METHOD_ROLE_WORDS, NONE_STATED, NOT_COMPARABLE,
  NO_PUBLICATION, NO_RECORD, OUTSIDE_REQUEST, PUBLICATION_ROLE_WORDS, RESOLUTION_WORDS, ROW_EFFECT_NULL, ROW_OUTCOME_TITLES,
  ROW_SIGN_NULL, ROW_TERMS, SCORES_NOTE, VALUE_KIND_WORDS, contextTermText, termText,
} from './panelWords'
import type { RowField } from './panelWords'
import { rowReleaseWords } from './releases'
import { RAW_COLUMNS_MAX, arrayOf, clipText, isHttpUrl, sourceText } from './text'

/** The hook's result. TYPES' own `GraphEvidenceQuery` (:626) is the evidence page's echo, so this name differs (M41). */
export type EvidenceQueryResult = ReturnType<typeof useGraphEvidence>
export interface EvidenceLine { text: string; href?: string }            // href only for isHttpUrl (2.4)
export interface EvidenceField { field: RowField; term: string; lines: readonly EvidenceLine[] }
export type RawView =
  | { kind: 'none' }
  | { kind: 'unavailable' }
  | { kind: 'columns'; entries: readonly (readonly [string, string])[]; hidden: number }   // at most RAW_COLUMNS_MAX entries (M36)
export interface EvidenceRowView {
  id: GraphEvidenceRef
  title: string
  outside: boolean                    // the outcome is not among the requested ones (U3)
  fields: EvidenceField[]             // 8.8's rows in its order, the shown ones only, `record` excluded
  record: string                      // NO_RECORD, or `${key}, line ${line_number} (${id})` (M64)
  raw: RawView
}
export interface EvidenceContext {
  edge: GraphEdge | null              // the shown edge (shownEdgeOf, 9): the type's polarity, orientation and type; null while none is known (M5)
  releases: ReadonlyMap<string, GraphRelease>
  requested: readonly GraphOutcome[]
  nodes: readonly GraphNode[]         // the relationship page's nodes, else the network's merged with the selection's (graphWithNodes, 3)
  presentation?: GraphPresentation | null
  vocabularies?: GraphVocabularies | null
}

function closedLabel(entries: readonly { value: string; label: string }[] | undefined, value: string): string | undefined {
  const found = Array.isArray(entries) ? entries.find((entry) => entry.value === value)?.label : undefined
  return typeof found === 'string' && found !== '' ? found : undefined
}

function glossed(label: string, gloss: string | undefined): string {
  return gloss === undefined ? label : `${label}: ${gloss}`
}

function isCriterion(item: unknown): item is { score_type: string; operator: string; value: string } {
  if (typeof item !== 'object' || item === null || Array.isArray(item)) return false
  const criterion = item as Record<string, unknown>
  return typeof criterion.score_type === 'string' && typeof criterion.operator === 'string' && typeof criterion.value === 'string'
}

/** As the source wrote them: "<2.2e-16", ">=" and "abs<" carry meaning and display as written. */
function thresholdText(threshold: unknown, outcome: string): string {
  if (!Array.isArray(threshold) || threshold.length === 0 || !threshold.every(isCriterion)) return clipText(sourceText(threshold))
  const criteria = threshold.map((item) => `${clipText(item.score_type)} ${clipText(item.operator)} ${clipText(item.value)}`)
  const tail = outcome === 'not_significant' ? '; this one does not' : ''
  return `As the source wrote them: ${criteria.join('; ')}. A result is significant only when it meets every criterion${tail}.`
}

/** Subject first: a symmetric row's sides are the source's order, so they are never read as a direction. */
function sideRank(side: string): number {
  return side === 'subject' ? 0 : 1
}

/**
 * While no edge is known, the words that read the edge's type come from the row itself: a row with
 * an `interaction_level` is physical, and a physical type states no effect. With an edge known, the
 * shown edge's words do not change when the relationship page arrives.
 */
export function evidenceRowView(row: GraphEvidenceRow, context: EvidenceContext): EvidenceRowView {
  const { edge, requested, nodes, presentation, vocabularies } = context
  const outside = !requested.includes(row.outcome)
  const title = `${own(ROW_OUTCOME_TITLES, row.outcome) ?? slugWords(row.outcome)}${outside ? OUTSIDE_REQUEST : ''}`
  const fields: EvidenceField[] = []
  const add = (field: RowField, lines: readonly (string | EvidenceLine)[], term: string = ROW_TERMS[field]) => {
    fields.push({ field, term, lines: lines.map((line) => (typeof line === 'string' ? { text: line } : line)) })
  }

  add('release', [rowReleaseWords(context.releases.get(row.release), row.release)])
  const basisLabel = closedLabel(vocabularies?.evidence_bases, row.evidence_basis) ?? slugWords(row.evidence_basis)
  add('basis', [glossed(basisLabel, own(BASIS_GLOSSES, row.evidence_basis))])
  const methodLabel = closedLabel(vocabularies?.assertion_methods, row.assertion_method) ?? slugWords(row.assertion_method)
  add('method', [glossed(methodLabel, own(METHOD_GLOSSES, row.assertion_method))])

  let effect: string
  if (row.effect !== null) effect = own(EFFECT_WORDS, row.effect) ?? slugWords(row.effect)
  else if (edge !== null ? edge.effect === 'not_applicable' : row.interaction_level !== null) effect = ROW_EFFECT_NULL.noPolarity
  else if (row.outcome !== 'supported') effect = ROW_EFFECT_NULL.notSupporting
  else effect = edge !== null ? ROW_EFFECT_NULL.notStated : NONE_STATED
  add('effect', [effect])
  add('effectAsStated', [
    row.effect_as_stated === '' ? "source's wording: not recorded" : `source's wording: ${clipText(row.effect_as_stated)}`,
  ])

  if (row.genetic_sign !== null || (edge !== null && edge.genetic_sign !== 'not_applicable')) {
    let sign: string
    if (row.genetic_sign !== null) sign = own(SIGN_WORDS, row.genetic_sign) ?? slugWords(row.genetic_sign)
    else sign = row.outcome !== 'supported' ? ROW_SIGN_NULL.notSupporting : ROW_SIGN_NULL.notStated
    add('sign', [sign])
  }
  if (row.interaction_type !== null) add('interactionType', [termText(row.interaction_type)])
  if (row.interaction_level !== null) {
    // The unspecified type claims no physical contact, so its rows never read 'physical interaction, level not stated'.
    let level: string
    if (edge !== null ? edge.type === 'protein_interaction_unspecified' : row.interaction_level === 'not_stated') level = 'level not stated'
    else if (row.interaction_level === 'association' && row.interaction_type === null) level = LEVEL_ASSIGNED_WORDS
    else level = levelWords(row.interaction_level, vocabularies)
    add('level', [level], row.outcome === 'supported' ? ROW_TERMS.level : LEVEL_TESTED_TERM)
  }
  if (row.relationship_as_stated !== '') add('relationshipAsStated', [clipText(row.relationship_as_stated)])

  const methods = arrayOf(row.methods)
  add('methods', methods.length === 0
    ? [NONE_STATED]
    : methods.map((method) => `${own(METHOD_ROLE_WORDS, method.role) ?? slugWords(method.role)}: ${termText(method)}`))
  if (row.eco_term !== null) add('eco', [termText(row.eco_term)])

  // The raw value, never the parsed `value`, which repeats it as a number or is null. A null model
  // and the type's own statement come only from the vocabularies' entry for that type.
  const scores = arrayOf(row.scores)
  if (scores.length === 0) {
    add('scores', [NONE_STATED])
  } else {
    const lines = scores.map((score) => {
      const entry = arrayOf(vocabularies?.score_types).find((candidate) => candidate.slug === score.type)
      const unit = typeof score.unit === 'string' && score.unit !== '' ? ` ${clipText(score.unit)}` : ''
      const censored = score.value === null ? CENSORED : ''
      const kind = own(VALUE_KIND_WORDS, score.value_kind) ?? slugWords(score.value_kind)
      const direction = own(DIRECTION_WORDS, score.direction) ?? slugWords(score.direction)
      const nullModel = typeof entry?.null_model === 'string' && entry.null_model !== '' ? `; null model: ${clipText(entry.null_model)}` : ''
      const comparable = entry !== undefined && entry.comparable_across_datasets === false ? NOT_COMPARABLE : ''
      return `${clipText(score.label)} (${clipText(score.type)}): ${clipText(score.raw_value)}${unit}${censored}; ${kind}, ${direction}${nullModel}${comparable}`
    })
    add('scores', [...lines, SCORES_NOTE])
  }

  const qualifiers: Record<string, unknown> = typeof row.qualifiers === 'object' && row.qualifiers !== null ? row.qualifiers : {}
  if (Object.prototype.hasOwnProperty.call(qualifiers, 'threshold')) add('threshold', [thresholdText(qualifiers.threshold, row.outcome)])
  const qualifierKeys = Object.keys(qualifiers).filter((key) => key !== 'threshold')
  if (qualifierKeys.length > 0) {
    add('qualifiers', qualifierKeys.map((key) => `${clipText(key)}: ${clipText(sourceText(qualifiers[key]))}`))
  }

  // An absent facet is "not stated", never "all".
  const contexts = arrayOf(row.contexts)
  add('contexts', contexts.length === 0 ? [NONE_STATED] : contexts.map((item) => {
    const facet = closedLabel(vocabularies?.context_facets, item.facet) ?? slugWords(item.facet)
    let value: string | null = null
    if (item.term !== null) {
      value = contextTermText(item.term)
    } else if (item.facet === 'host_taxon' && item.taxon !== null) {
      const taxon = arrayOf(vocabularies?.taxa).find((candidate) => candidate.taxid === item.taxon)
      value = taxon === undefined ? `taxid ${item.taxon}` : `${clipText(taxon.scientific_name)} (taxid ${item.taxon})`
    } else if (item.label !== '') {
      value = clipText(item.label)
    }
    return value === null ? `${facet}: ${clipText(item.raw_value)}` : `${facet}: ${value} (as written: ${clipText(item.raw_value)})`
  }))

  const roles = edge === null ? null : roleLabels(edge.type, vocabularies)
  const symmetric = edge !== null && edge.orientation === 'symmetric'
  const participants = [...arrayOf(row.participants)].sort((a, b) => sideRank(a.side) - sideRank(b.side))
  add('participants', participants.map((participant) => {
    let side: string = participant.side
    if (symmetric) side = 'participant'
    else if (roles !== null) side = participant.side === 'subject' ? roles.subject : roles.object
    const node = nodes.find((candidate) => candidate.id === participant.entity)
    const name = node === undefined ? participant.entity : clipText(nodeLabel(node, presentation))
    const resolution = own(RESOLUTION_WORDS, participant.resolution_basis) ?? `(${slugWords(participant.resolution_basis)})`
    // The isoform the source named has no node in the page, so it shows as its ref.
    const isoform = participant.stated_entity === null ? '' : `; the isoform named: ${participant.stated_entity}`
    return `${clipText(participant.identifier_as_stated)} (${side}, as written), resolved to ${name} ${resolution}${isoform}`
  }))

  if (row.evidence_group !== '') {
    add('group', [`${clipText(row.evidence_group)}: results the source groups as one observation in this release`])
  }

  const publicationLines: EvidenceLine[] = []
  for (const { role, publication } of arrayOf(row.publications)) {
    const synthetic = publication.is_synthetic ? ' (synthetic)' : ''
    const paperTitle = clipText(publication.title || 'title not recorded')
    publicationLines.push({ text: `${own(PUBLICATION_ROLE_WORDS, role) ?? slugWords(role)}: ${paperTitle}${synthetic}` })
    const about = [publication.authors, publication.journal, publication.year === null ? '' : String(publication.year)]
      .filter((part) => typeof part === 'string' && part !== '')
      .map((part) => clipText(part))
    if (about.length > 0) publicationLines.push({ text: about.join(', ') })
    const identifiers: string[] = []
    if (publication.pmid !== null) identifiers.push(`PMID ${clipText(publication.pmid)}`)
    if (publication.doi !== null) identifiers.push(`DOI ${clipText(publication.doi)}`)
    if (publication.pmcid !== null) identifiers.push(`PMCID ${clipText(publication.pmcid)}`)
    if (publication.imex_id !== null) identifiers.push(`IMEx ${clipText(publication.imex_id)}`)
    if (publication.local_key !== null) identifiers.push(`local key ${clipText(publication.local_key)}`)
    if (identifiers.length > 0) publicationLines.push({ text: identifiers.join('; ') })
    const url = publication.url
    if (isHttpUrl(url)) publicationLines.push({ text: clipText(url), href: url })
    else if (typeof url === 'string' && url !== '') publicationLines.push({ text: clipText(url) })
  }
  add('publications', publicationLines.length === 0 ? [NO_PUBLICATION] : publicationLines)

  // Keys are never sorted: they keep the parsed object's order, which is the API's for every column
  // name a format allows. One record may hold any number of columns, so at most RAW_COLUMNS_MAX show.
  const record = row.record
  let raw: RawView
  if (record === null) {
    raw = { kind: 'none' }
  } else if (record.raw === null) {
    raw = { kind: 'unavailable' }
  } else {
    const source = record.raw
    const keys = Object.keys(source)
    raw = {
      kind: 'columns',
      entries: keys.slice(0, RAW_COLUMNS_MAX).map((key) => [clipText(key), clipText(sourceText(source[key]))] as const),
      hidden: Math.max(0, keys.length - RAW_COLUMNS_MAX),
    }
  }
  return {
    id: row.id,
    title,
    outside,
    fields,
    // The row's own id: one record backs several rows, so the id names the row.
    record: record === null ? NO_RECORD : `${clipText(record.key)}, line ${record.line_number} (${row.id})`,
    raw,
  }
}

export interface EvidencePages {
  rows: GraphEvidenceRow[]
  releases: GraphRelease[]
  publications: GraphPublication[]
  bad: boolean
  repeated: boolean                   // the last page's next is a ref already sent as after (M35)
}

/**
 * Only a repeated id is dropped, never a row that is not an object, whose read throws for the
 * panel's boundary to catch. A `next` already sent as `after` would grow the list for ever.
 */
export function evidencePagesOf(pages: readonly unknown[] | undefined, pageParams?: readonly unknown[]): EvidencePages {
  const out: EvidencePages = { rows: [], releases: [], publications: [], bad: false, repeated: false }
  const list = Array.isArray(pages) ? pages : []
  const seen = new Set<string>()
  for (const page of list) {
    if (!isEvidencePage(page)) return { rows: [], releases: [], publications: [], bad: true, repeated: false }
    for (const row of arrayOf(page.evidence)) {
      if (seen.has(row.id)) continue
      seen.add(row.id)
      out.rows.push(row)
    }
    for (const release of arrayOf(page.releases)) out.releases.push(release)
  }
  for (const row of out.rows) {
    for (const entry of arrayOf(row.publications)) out.publications.push(entry.publication)
  }
  const last = list.length > 0 ? list[list.length - 1] : undefined
  if (isEvidencePage(last)) {
    const next = last.page.next
    out.repeated = typeof next === 'string' && arrayOf(pageParams).includes(next)
  }
  return out
}

/**
 * A list that mounts during a fetch has seen no fetch settle, and the infinite query's flags name
 * the direction of its running fetch, not of the one that failed, so the request a stored error
 * answered says which fetch failed. An HTTP or network failure is axios's error, which keeps the
 * params it was sent with, and a later page is sent with the last loaded page's `next`; a re-read
 * starts again from the first page and follows the cursors it reads, so it sends that `next` only
 * when the pages it reads end where the loaded ones did. `null` when the error cannot say: it names
 * no request (a body the client could not read, a line too long to send, a cancelled fetch), or a
 * cursor that is not a string, which the API never sends and axios may hand back as a copy, so no
 * comparison with a page's `next` can be trusted to place it.
 */
export function laterPageRequested(error: unknown, pages: readonly unknown[] | undefined): boolean | null {
  if (!isAxiosError(error)) return null
  const params: unknown = error.config?.params
  if (typeof params !== 'object' || params === null) return null
  const after = 'after' in params ? params.after : undefined
  if (after !== undefined && typeof after !== 'string') return null
  const last = pages !== undefined && pages.length > 0 ? pages[pages.length - 1] : undefined
  return typeof after === 'string' && isEvidencePage(last) && after === last.page.next
}

/** Every outcome counts, one a deploy brings early included, since the list takes every filter but `outcome`. */
export function evidenceTotal(edge: GraphEdge | null): number | null {
  if (edge === null || edge === undefined) return null
  const counts: Record<string, unknown> = typeof edge.outcome_counts === 'object' && edge.outcome_counts !== null ? edge.outcome_counts : {}
  let total = 0
  for (const count of Object.values(counts)) {
    if (typeof count === 'number') total += count
  }
  return total
}
