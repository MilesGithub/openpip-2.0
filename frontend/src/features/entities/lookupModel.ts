import { classifyGraphError } from '../../api/graph/errors'
import type { GraphResolveParams, GraphSelection } from '../../api/graph/params'
import { routeErrorView } from '../graph/pages/routeState'
import { isSyntheticShown } from '../graph/panels/releases'
import { arrayOf, clipText } from '../graph/panels/text'
import { chunkOutcome } from '../graph/search/searchModel'
import type { GraphCandidate, GraphEntityRef, GraphRelease, GraphVocabularies } from '../graph/types'
import { ENTITY_SELECTION_NAMES, isEntityRef } from './entityAddress'
import type { EntitySelectionName } from './entityAddress'
import {
  EMPTY_VALUE, LOOKUP_ERROR, LOOKUP_SEARCHING, LOOKUP_TIMEOUT, chooseText, controlValueText, longValueText, lookupProblemText,
  lookupUnmatchedText, separatorValueText,
} from './entityWords'

export type LookupRefusal = 'separator' | 'control' | 'empty' | 'long'

function isControlPoint(point: string): boolean {
  // Unicode's Cc, as the API refuses it (backend/graph/api/params.py:157).
  const code = point.codePointAt(0) ?? 0
  return code <= 0x1f || (code >= 0x7f && code <= 0x9f)
}

export function lookupRefusal(value: string, maxChars: number): LookupRefusal | null {
  // Found by code point, never by a regular expression (no-control-regex). The API splits a term at commas, tabs and line
  // breaks (backend/graph/api/params.py:37) and counts its code points once trimmed (:139-155).
  const points = Array.from(value)
  if (points.some((point) => point === ',' || point === '\t' || point === '\n' || point === '\r')) return 'separator'
  if (points.some(isControlPoint)) return 'control'
  const kept = Array.from(value.trim())
  if (kept.length === 0) return 'empty'
  return kept.length > maxChars ? 'long' : null
}

export function lookupParams(namespace: string, value: string, selection: GraphSelection): GraphResolveParams {
  // One term in one namespace (Q38): with a namespace, resolve reads that namespace only and searches no label
  // (backend/graph/resolution.py:145-150).
  return typeof selection.browser === 'string'
    ? { q: [value], namespace, browser: selection.browser }
    : { q: [value], namespace, releases: selection.releases }
}

export function namespaceLabel(namespace: string, vocabularies: GraphVocabularies | null | undefined): string | null {
  // Compared as written (Q38): a slug in another case is no slug, and the API's 400 then names it.
  const label: unknown = arrayOf(vocabularies?.namespaces).find((entry) => entry.slug === namespace)?.label
  return typeof label === 'string' && label !== '' ? clipText(label) : null
}

export type LookupOutcome =
  | { kind: 'searching' }
  | { kind: 'resolved'; ref: GraphEntityRef }
  // releases: the answer's, as searchModel.ts's answered reads them, for the synthetic badge (Q25)
  | { kind: 'choose'; status: string; candidates: readonly GraphCandidate[]; truncated: boolean; releases: readonly GraphRelease[] }
  | { kind: 'unmatched' }
  | { kind: 'refused'; problems: readonly string[] }   // the API's 400: its errors[].parameter values, each once, in order
  | { kind: 'failed'; error: unknown }                  // any other failure; null: a body the guard refuses

export function lookupOutcome(query: { data: unknown; error: unknown; isFetching: boolean; isPaused: boolean }): LookupOutcome {
  // The search's guard (searchModel.ts): one result, every candidate a node with a string id, or nothing is shown.
  const chunk = chunkOutcome({ data: query.data, error: query.error, isFetching: query.isFetching || query.isPaused, size: 1 })
  if (chunk.kind === 'searching') return { kind: 'searching' }
  if (chunk.kind === 'error') {
    const info = chunk.error === null || chunk.error === undefined ? null : classifyGraphError(chunk.error)
    if (info !== null && info.kind === 'bad_request') {
      const names = info.errors.flatMap((problem) => (typeof problem?.parameter === 'string' ? [problem.parameter] : []))
      return { kind: 'refused', problems: names.filter((name, index) => names.indexOf(name) === index) }
    }
    return { kind: 'failed', error: chunk.error ?? null }
  }
  const result = chunk.results[0]
  const candidates = arrayOf(result.candidates)
  // No ref outside the grammar reaches a link or the redirect: such a body reads as one the guard refuses.
  if (candidates.some((candidate) => !isEntityRef(candidate.entity.id))) return { kind: 'failed', error: null }
  // One result, resolved, with its one entity, and nothing else, is followed (Q38; ARCH:1877-1884). A resolved result
  // with more is a choice, as the search's termOutcomes reads it: nothing takes the first of several (ARCH:801-802).
  if (result.status === 'resolved' && candidates.length === 1) return { kind: 'resolved', ref: candidates[0].entity.id }
  if (result.status === 'unmatched' || candidates.length === 0) return { kind: 'unmatched' }
  return { kind: 'choose', status: result.status, candidates, truncated: result.truncated === true, releases: chunk.releases }
}

export interface LookupInput {
  value: string                    // the address's value, as React Router decodes it
  label: string | null             // namespaceLabel's
  maxChars: number                 // searchLimits(status).maxTermChars: status.limits.q_max_chars, else 200
  scoped: boolean                  // the address names releases or browser, within which resolve matched
  refusal: LookupRefusal | null    // lookupRefusal's
  outcome: LookupOutcome | null    // lookupOutcome's; null while the value is refused
}
export interface LookupState {
  line: string                                 // the status line's words
  ready: boolean                               // whether the h1 may take the arrival focus (J30)
  candidates: readonly GraphCandidate[]        // the list, in the API's order; [] but for choose
  redirect: GraphEntityRef | null              // resolved: the entity the address is replaced with
  removal: readonly EntitySelectionName[]      // the selection names a 400 named, for a link without them
  retry: boolean                               // whether "Try again" is offered
  synthetic: boolean                           // choose only: its answer drew on a synthetic release, so the badge shows
}

export function lookupState(input: LookupInput): LookupState {
  const { value, label, maxChars, scoped, refusal, outcome } = input
  const blank: LookupState = { line: '', ready: true, candidates: [], redirect: null, removal: [], retry: false, synthetic: false }
  if (refusal === 'separator') return { ...blank, line: separatorValueText(value) }
  if (refusal === 'control') return { ...blank, line: controlValueText(value) }
  if (refusal === 'empty') return { ...blank, line: EMPTY_VALUE }
  if (refusal === 'long') return { ...blank, line: longValueText(value, maxChars) }
  if (outcome === null || outcome.kind === 'searching') return { ...blank, line: LOOKUP_SEARCHING, ready: false }
  switch (outcome.kind) {
    case 'resolved':
      return { ...blank, line: LOOKUP_SEARCHING, ready: false, redirect: outcome.ref }
    case 'choose':
      return {
        ...blank,
        line: chooseText(outcome.status, value, outcome.truncated ? outcome.candidates.length : null),
        candidates: outcome.candidates,
        // Every page showing these data carries the badge (ARCH:1786-1788); a redirect needs none, as the entity page has its own.
        synthetic: isSyntheticShown({ releases: outcome.releases }),
      }
    case 'unmatched':
      return { ...blank, line: lookupUnmatchedText(value, label, scoped) }
    case 'refused':
      return {
        ...blank,
        line: lookupProblemText(outcome.problems),
        removal: ENTITY_SELECTION_NAMES.filter((name) => outcome.problems.includes(name)),
      }
    case 'failed': {
      if (outcome.error === null) return { ...blank, line: LOOKUP_ERROR, retry: true }
      const failure = routeErrorView(outcome.error, { other: LOOKUP_ERROR, notFound: null, timeout: LOOKUP_TIMEOUT })
      return { ...blank, line: failure.text, retry: failure.retry }
    }
  }
}
