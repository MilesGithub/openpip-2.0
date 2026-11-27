import { classifyGraphError, graphMayResend } from '../../../api/graph/errors'
import { GRAPH_RESOLVE_MAX_TERMS } from '../../../api/graph/resolve'
import { entityTypeLabel } from '../network/words'
import { panelMayRetry } from '../panels/panelWords'
import { arrayOf, clipText } from '../panels/text'
import type {
  GraphCandidate, GraphEntityRef, GraphMatch, GraphNode, GraphRelease, GraphResolveResult, GraphStatus, GraphVocabularies,
} from '../types'
import { taxonWords } from '../words/taxa'

export const SEARCH_TERM_MAX_CHARS = 200
export interface SearchLimits { maxTerms: number; maxTermChars: number }

function whole(value: unknown, fallback: number): number {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= 1 ? value : fallback
}

export function searchLimits(status: GraphStatus | null | undefined): SearchLimits {
  return {
    maxTerms: whole(status?.limits?.resolve_max_terms, GRAPH_RESOLVE_MAX_TERMS),
    maxTermChars: whole(status?.limits?.q_max_chars, SEARCH_TERM_MAX_CHARS),
  }
}

export type TermRefusal = 'control' | 'long' | 'unsendable'
export interface TermEntry { term: string; refused: TermRefusal | null }
export interface SplitSearch { entries: TermEntry[]; sent: string[]; over: number }

function isControl(code: number): boolean {
  return code <= 0x1f || (code >= 0x7f && code <= 0x9f)
}

/** A lone surrogate, which Array.from yields alone: encoding the query throws before anything is sent (7.10). */
function isSurrogate(code: number): boolean {
  return code >= 0xd800 && code <= 0xdfff
}

export function splitSearch(text: string, limits: SearchLimits): SplitSearch {
  const pieces: string[] = []
  let piece = ''
  for (const character of Array.from(text)) {
    const code = character.codePointAt(0) ?? 0
    if (character === ',' || code === 0x09 || code === 0x0a || code === 0x0d) {
      pieces.push(piece)
      piece = ''
    } else {
      piece += character
    }
  }
  pieces.push(piece)
  const distinct: string[] = []
  for (const raw of pieces) {
    const term = raw.trim()
    if (term !== '' && distinct.indexOf(term) === -1) distinct.push(term)
  }
  const kept = distinct.slice(0, limits.maxTerms)
  const entries = kept.map((term): TermEntry => {
    const characters = Array.from(term)
    if (characters.some((character) => isControl(character.codePointAt(0) ?? 0))) return { term, refused: 'control' }
    if (characters.some((character) => isSurrogate(character.codePointAt(0) ?? 0))) return { term, refused: 'unsendable' }
    if (characters.length > limits.maxTermChars) return { term, refused: 'long' }
    return { term, refused: null }
  })
  return { entries, sent: entries.filter((entry) => entry.refused === null).map((entry) => entry.term), over: distinct.length - kept.length }
}

export type ChunkOutcome =
  | { kind: 'searching' }
  | { kind: 'error'; error: unknown }   // error null: a body the guard refuses
  | { kind: 'answered'; results: readonly GraphResolveResult[]; cut: number; releases: readonly GraphRelease[] }

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function isEntity(value: unknown): boolean {
  if (!isObject(value)) return false
  const primary = value.primary_identifier
  return typeof value.id === 'string' && typeof value.label === 'string' && typeof value.type === 'string'
    && (!isObject(primary) || typeof primary.value === 'string')
}

function isResult(value: unknown): boolean {
  if (!isObject(value) || typeof value.status !== 'string' || !Array.isArray(value.candidates)) return false
  const candidates: unknown[] = value.candidates
  return candidates.every((candidate) => isObject(candidate) && isEntity(candidate.entity))
}

export function chunkOutcome(input: { data: unknown; error: unknown; isFetching: boolean; size: number }): ChunkOutcome {
  if (input.isFetching && input.data === undefined) return { kind: 'searching' }
  if (input.error !== null && input.error !== undefined) return { kind: 'error', error: input.error }
  const data = input.data
  if (!isObject(data) || !Array.isArray(data.results) || data.results.length !== input.size) return { kind: 'error', error: null }
  const results: unknown[] = data.results
  if (!results.every(isResult)) return { kind: 'error', error: null }
  const page = isObject(data.page) ? data.page : null
  const omitted = page !== null && isObject(page.omitted) ? page.omitted : null
  const terms: unknown = omitted === null ? undefined : omitted.terms
  const cut = typeof terms === 'number' && Number.isSafeInteger(terms) && terms >= 0 ? terms : 0
  // Only is_synthetic is read, by the search's badge (7.11).
  const listed: unknown[] = Array.isArray(data.releases) ? data.releases : []
  const releases = listed.filter((release): release is GraphRelease => isObject(release) && typeof release.is_synthetic === 'boolean')
  return { kind: 'answered', results: results as GraphResolveResult[], cut, releases }
}

export type TermOutcome =
  | { kind: 'refused'; reason: TermRefusal }
  | { kind: 'searching' }
  | { kind: 'failed'; error: unknown }
  | { kind: 'resolved'; candidate: GraphCandidate }
  | { kind: 'choose'; status: string; candidates: readonly GraphCandidate[]; truncated: boolean }
  | { kind: 'unmatched' }
export interface TermResult { entry: TermEntry; outcome: TermOutcome; chunk: number | null }

export function termOutcomes(split: SplitSearch, chunks: readonly (readonly string[])[], outcomes: readonly ChunkOutcome[]): TermResult[] {
  const place = new Map<string, [number, number]>()
  chunks.forEach((chunk, i) => chunk.forEach((term, j) => place.set(term, [i, j])))
  return split.entries.map((entry): TermResult => {
    if (entry.refused !== null) return { entry, outcome: { kind: 'refused', reason: entry.refused }, chunk: null }
    const at = place.get(entry.term)
    const outcome = at === undefined ? undefined : outcomes[at[0]]
    if (at === undefined || outcome === undefined) return { entry, outcome: { kind: 'failed', error: null }, chunk: at === undefined ? null : at[0] }
    if (outcome.kind === 'searching') return { entry, outcome: { kind: 'searching' }, chunk: at[0] }
    if (outcome.kind === 'error') return { entry, outcome: { kind: 'failed', error: outcome.error }, chunk: at[0] }
    const result = outcome.results[at[1]]
    if (result.status === 'resolved' && result.candidates.length === 1) {
      return { entry, outcome: { kind: 'resolved', candidate: result.candidates[0] }, chunk: at[0] }
    }
    if (result.status === 'unmatched' || result.candidates.length === 0) return { entry, outcome: { kind: 'unmatched' }, chunk: at[0] }
    return {
      entry,
      outcome: { kind: 'choose', status: result.status, candidates: result.candidates, truncated: result.truncated === true },
      chunk: at[0],
    }
  })
}

export function autoAdds(outcomes: readonly TermOutcome[], chosen: readonly GraphEntityRef[], max: number): GraphNode[] {
  const kept: GraphNode[] = []
  for (const outcome of outcomes) {
    if (outcome.kind !== 'resolved') continue
    const entity = outcome.candidate.entity
    if (chosen.indexOf(entity.id) !== -1 || kept.some((node) => node.id === entity.id)) continue
    if (chosen.length + kept.length >= max) break
    kept.push(entity)
  }
  return kept
}

export function searchMayRetry(error: unknown): boolean {
  if (error === null) return true
  // An error not from axios is a client-side failure that a repeat does not change.
  if (classifyGraphError(error).kind === 'unknown') return false
  return panelMayRetry(classifyGraphError(error)) && graphMayResend({ state: { error } })
}

export interface SearchOption { value: string; text: string }

export function entityTypeOptions(vocabularies: GraphVocabularies | null | undefined): SearchOption[] {
  const options: SearchOption[] = []
  for (const entry of arrayOf(vocabularies?.entity_types)) {
    const slug: unknown = entry?.slug
    if (typeof slug !== 'string' || slug === '' || options.some((option) => option.value === slug)) continue
    options.push({ value: slug, text: clipText(entityTypeLabel(slug, vocabularies)) })
  }
  return options
}

export function searchTaxonOptions(vocabularies: GraphVocabularies | null | undefined): SearchOption[] {
  const options: SearchOption[] = []
  for (const entry of arrayOf(vocabularies?.taxa)) {
    const taxid: unknown = entry?.taxid
    if (typeof taxid !== 'number' || !Number.isSafeInteger(taxid) || options.some((option) => option.value === String(taxid))) continue
    options.push({ value: String(taxid), text: taxonWords(taxid, vocabularies) })
  }
  return options
}

export function candidateOrganism(candidate: GraphCandidate, vocabularies: GraphVocabularies | null | undefined): string | null {
  const taxon: unknown = candidate.entity.taxon
  return typeof taxon === 'number' && Number.isSafeInteger(taxon) ? taxonWords(taxon, vocabularies) : null
}

export function candidateMatch(candidate: GraphCandidate): GraphMatch | null {
  const matches: unknown = candidate.matches
  if (!Array.isArray(matches)) return null
  const first: unknown = matches[0]
  return isObject(first) && typeof first.kind === 'string' ? (first as unknown as GraphMatch) : null
}
