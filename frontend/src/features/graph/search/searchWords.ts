import type { CSSProperties } from 'react'
import { classifyGraphError } from '../../../api/graph/errors'
import { own } from '../network/graph'
import { plural, slugWords } from '../network/words'
import { INPUT_STYLE } from '../pages/pageStyle'
import { SERVER_UNREACHABLE, throttledText } from '../pages/pageWords'
import { clipText } from '../panels/text'
import type { GraphMatch, GraphMatchKind } from '../types'
import { isolateText } from '../words/isolateText'
import type { TermOutcome } from './searchModel'

export const SEARCH_FORM_NAME = 'Search for entities'
export const TERMS_LABEL = 'Names or identifiers'
export const TERMS_HINT = 'Separate several with commas or new lines. A name that holds a comma or a tab cannot be searched: use an identifier.'
export const ENTITY_TYPE_LABEL = 'Entity type'
export const ANY_ENTITY_TYPE = 'Any entity type'
export const SEARCH_TAXON_LABEL = 'Organism of the entities to find'
export const SEARCH_ANY_ORGANISM = 'Any organism'
export const SEARCH_BUTTON = 'Search'
export const SEARCHING = 'Searching…'
export const RESULTS_NAME = 'Search results'

export function searchedText(n: number): string {
  return plural(n, '1 term searched.', '{n} terms searched.')
}

export const NO_TERM_SENT = 'No term was searched.'

export function overCapText(extra: number, max: number): string {
  return `${plural(extra, '1 more term was not searched', '{n} more terms were not searched')}: ${plural(max, 'a search takes at most 1 term.', 'a search takes at most {n} terms.')}`
}

export function cutText(n: number): string {
  return plural(n, 'For 1 term more entities match than are listed: search with an identifier to narrow it.',
    'For {n} terms more entities match than are listed: search with identifiers to narrow them.')
}

export function failedText(n: number): string {
  return plural(n, '1 term could not be searched.', '{n} terms could not be searched.')
}

export const SEARCH_TIMEOUT = 'The search did not finish in time.'
export const SEARCH_TOO_LONG = 'The search would be too long to send: search with shorter terms or fewer releases.'

export function searchFailureWords(error: unknown): string {
  if (error === null || error === undefined) return ''
  const info = classifyGraphError(error)
  switch (info.kind) {
    case 'throttled':
      return throttledText(info.retryAfter)
    case 'network':
      return SERVER_UNREACHABLE
    case 'timeout':
      return SEARCH_TIMEOUT
    case 'too_long':
    case 'server_refused':
      return SEARCH_TOO_LONG
    default:
      return ''
  }
}

export interface SearchSummary {
  submitted: boolean; searching: boolean; searched: number; over: number; maxTerms: number; cut: number; failed: number; failure: unknown
  chosen: number; notAdded: number; adding: number; choose: number; unmatched: number
}

export function outcomeCountsText(summary: SearchSummary): string {
  const parts: string[] = []
  if (summary.chosen > 0) parts.push(plural(summary.chosen, '1 is among the chosen entities', '{n} are among the chosen entities'))
  if (summary.adding > 0) parts.push(plural(summary.adding, '1 is being added', '{n} are being added'))
  if (summary.notAdded > 0) {
    parts.push(plural(summary.notAdded, '1 was not added: the network takes no more', '{n} were not added: the network takes no more'))
  }
  if (summary.choose > 0) parts.push(plural(summary.choose, '1 needs a choice from its list below', '{n} need a choice from their lists below'))
  if (summary.unmatched > 0) {
    parts.push(plural(summary.unmatched, '1 matched no entity available to you', '{n} matched no entity available to you'))
  }
  return parts.length === 0 ? '' : `${parts.join('; ')}.`
}

export function searchStatusText(summary: SearchSummary): string {
  if (!summary.submitted) return ''
  if (summary.searching) return SEARCHING
  const parts = [summary.searched === 0 ? NO_TERM_SENT : searchedText(summary.searched)]
  const counts = outcomeCountsText(summary)
  if (counts !== '') parts.push(counts)
  if (summary.over > 0) parts.push(overCapText(summary.over, summary.maxTerms))
  if (summary.cut > 0) parts.push(cutText(summary.cut))
  if (summary.failed > 0) {
    parts.push(failedText(summary.failed))
    const words = searchFailureWords(summary.failure)
    if (words !== '') parts.push(words)
  }
  return parts.join(' ')
}

export function resolvedChosenText(term: string, name: string): string {
  return `“${isolateText(term)}” is ${isolateText(name)}, among the chosen entities.`
}

export function resolvedNotChosenText(term: string, name: string): string {
  return `“${isolateText(term)}” is ${isolateText(name)}, not among the chosen entities. Press Search to add it.`
}

export function resolvedAddingText(term: string, name: string): string {
  return `“${isolateText(term)}” is ${isolateText(name)}; adding it…`
}

export function limitText(max: number): string {
  return `${plural(max, 'The network takes at most 1 entity.', 'The network takes at most {n} entities.')} Remove one to add another.`
}

export function resolvedLimitText(term: string, name: string, max: number): string {
  return `“${isolateText(term)}” is ${isolateText(name)}, not added. ${limitText(max)}`
}

export function candidateText(term: string): string {
  return `“${isolateText(term)}” matches one entity, though not as its primary identifier is written. Add it if it is the one you meant.`
}

export function ambiguousText(term: string): string {
  return `“${isolateText(term)}” matches more than one entity. Add the one you meant.`
}

export function chosenText(name: string): string {
  return `${isolateText(name)} is among the chosen entities.`
}

export function truncatedText(listed: number): string {
  return `More entities match than the ${listed} listed; search with an identifier to narrow them.`
}

export function unmatchedText(term: string, organism: string | null, scoped: boolean): string {
  const scope = scoped ? ' in the releases this view reads' : ''
  return organism === null
    ? `No entity available to you${scope} matches “${isolateText(term)}”.`
    : `No ${isolateText(organism)} entity available to you${scope} matches “${isolateText(term)}”.`
}

export const PREFIX_SHAPE = /^[A-Za-z][A-Za-z0-9._-]*:./
export const PREFIX_NOTE =
  "A term that starts with a word and a colon is read as an identifier with its namespace's prefix: to find a name written that way, search for an identifier."

export function controlTermText(term: string): string {
  return `“${isolateText(clipText(term))}” holds a control character, so it was not searched.`
}

export function unsendableTermText(term: string): string {
  return `“${isolateText(clipText(term))}” holds a character that cannot be sent, so it was not searched.`
}

export function longTermText(term: string, max: number): string {
  return `“${isolateText(clipText(term))}” is longer than ${plural(max, '1 character', '{n} characters')}, so it was not searched.`
}

export function termSearchingText(term: string): string {
  return `Searching for “${isolateText(term)}”…`
}

export function termFailedText(term: string): string {
  return `“${isolateText(term)}” could not be searched.`
}

export function matchesName(term: string): string {
  return `Entities matching “${isolateText(term)}”`
}

export function addText(name: string): string {
  return `Add ${isolateText(name)}`
}

export const CHOSEN_MARK = ' (chosen)'
export const MATCH_KIND_WORDS: Readonly<Record<GraphMatchKind, string>> = {
  primary: 'its primary identifier', secondary: 'a secondary identifier', xref: 'a cross-reference', symbol: 'a symbol', alias: 'an alias',
  label: 'its label',
}
export const CASE_NOTE = ' in another case'
export const VERSION_NOTE = ' with another version'

export function matchText(match: GraphMatch): string {
  return `matched by ${own(MATCH_KIND_WORDS, match.kind) ?? slugWords(match.kind)}${match.case_exact === false ? CASE_NOTE : ''}${match.version_mismatch === true ? VERSION_NOTE : ''}`
}

export function candidateFactsText(organism: string | null, match: GraphMatch | null): string {
  return [organism, match === null ? null : matchText(match)].filter((part): part is string => part !== null).join('; ')
}

export interface TermContext {
  name: string | null; chosen: boolean; atLimit: boolean; max: number; pending: boolean; pickedName: string | null; organism: string | null
  scoped: boolean; maxTermChars: number
}

export function termLineText(term: string, outcome: TermOutcome, context: TermContext): string {
  switch (outcome.kind) {
    case 'refused':
      if (outcome.reason === 'control') return controlTermText(term)
      if (outcome.reason === 'unsendable') return unsendableTermText(term)
      return longTermText(term, context.maxTermChars)
    case 'searching':
      return termSearchingText(term)
    case 'failed':
      return termFailedText(term)
    case 'resolved': {
      const name = context.name ?? ''
      if (context.chosen) return resolvedChosenText(term, name)
      if (context.atLimit) return resolvedLimitText(term, name, context.max)
      if (context.pending) return resolvedAddingText(term, name)
      return resolvedNotChosenText(term, name)
    }
    case 'choose': {
      let text = outcome.status === 'candidate' ? candidateText(term) : ambiguousText(term)
      if (context.pickedName !== null) text = `${text} ${chosenText(context.pickedName)}`
      if (outcome.truncated) text = `${text} ${truncatedText(outcome.candidates.length)}`
      if (context.atLimit) text = `${text} ${limitText(context.max)}`
      return text
    }
    case 'unmatched':
      return PREFIX_SHAPE.test(term)
        ? `${unmatchedText(term, context.organism, context.scoped)} ${PREFIX_NOTE}`
        : unmatchedText(term, context.organism, context.scoped)
    default:
      return ''
  }
}

export const TERMS_FIELD_STYLE: CSSProperties = { display: 'flex', flexDirection: 'column', gap: 4, minWidth: 0, flex: '1 1 320px' }
export const TERMS_BOX_STYLE: CSSProperties = { ...INPUT_STYLE, width: '100%', boxSizing: 'border-box', resize: 'vertical' }
export const RESULT_LINE_STYLE: CSSProperties = { margin: '4px 0' }
export const CANDIDATE_LIST_STYLE: CSSProperties = { marginLeft: 16 }
export const FACTS_STYLE: CSSProperties = { margin: '0 0 4px', color: 'var(--text-muted)' }
