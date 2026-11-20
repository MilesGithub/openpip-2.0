import { classifyGraphError } from '../../api/graph/errors'
import { plural } from '../graph/network/words'
import { SERVER_UNREACHABLE, throttledText } from '../graph/pages/pageWords'
import { panelMayRetry } from '../graph/panels/panelWords'
import type { GraphCandidate } from '../graph/types'
import { isolateText } from '../graph/words/isolateText'

// The one-term seed search's words and rules (J1). Each name and term a sentence places is isolated, so a
// bidirectional control inside it cannot reorder the words around it (J31).

export const SEED_SECTION_HEADING = 'Find entities in this browser'
export const SEED_FORM_NAME = 'Search this browser'
export const SEED_LABEL = 'Name or identifier'
export const SEED_BUTTON = 'Search'
export const SEARCHING = 'Searching…'
/** The API splits a term on commas, tabs and new lines, so such a term is refused before sending. */
export const ONE_TERM = 'Search for one name or identifier at a time. A name that holds a comma or tab cannot be searched: use an identifier.'

export function chosenText(name: string): string {
  return `${isolateText(name)} is among the chosen entities.`
}

/** True after a removal, Back, a pasted address or an add refused at the limit: a new submit adds the entity from the cache. */
export function notChosenText(name: string): string {
  return `${isolateText(name)} is not among the chosen entities. Press Search to add it.`
}

export function candidateText(term: string): string {
  return `“${isolateText(term)}” matches one entity, though not as its primary identifier is written. Add it if it is the one you meant.`
}

export function ambiguousText(term: string): string {
  return `“${isolateText(term)}” matches more than one entity. Add the one you meant.`
}

/** With `taxon` sent, resolve keeps only that taxid's entities, so the sentence names the organism. */
export function unmatchedText(term: string, organism: string | null): string {
  return organism === null
    ? `No entity in this browser matches “${isolateText(term)}”.`
    : `No ${isolateText(organism)} entity in this browser matches “${isolateText(term)}”.`
}

export const PREFIX_NOTE =
  "A term that starts with a word and a colon is read as an identifier with its namespace's prefix: to find a name written that way, search for an identifier."

export function truncatedText(listed: number): string {
  return `More entities match than the ${listed} listed; search with an identifier to narrow them.`
}

export function limitText(max: number): string {
  return `${plural(max, 'The network takes at most 1 entity.', 'The network takes at most {n} entities.')} Remove one to add another.`
}

export const CANDIDATES_NAME = 'Matching entities'

export function addText(name: string): string {
  return `Add ${isolateText(name)}`
}

export const CHOSEN_MARK = ' (chosen)'
export const SEARCH_FAILED = 'The search could not be run.'
export const SEARCH_TIMEOUT = 'The search did not finish in time.'
export const SEARCH_AGAIN = 'Press Search to try again.'
export const NO_SEED_PROMPT = 'Search for an entity to draw its network in this browser.'
export const SEEDS_ADDRESS_ERROR = 'This page cannot use the entities named in this address.'
export const SEEDS_REMOVAL = 'Show this browser with no entities chosen'
export const NETWORK_HEADING = 'Network'
export const CHOSEN_LABEL = 'Chosen entities'

export function removeText(name: string): string {
  return `Remove ${isolateText(name)}`
}

export const REMOVE_ALL = 'Remove all chosen entities'

export const SEED_TERM_MAX = 200     // the API's Q_MAX_CHARS for a resolve term (params.py:146-153)
/** A term the API reads as a namespace's prefix and a value (PAGE:512-515): a letter, then letters, digits, `.`, `_` or `-`, then a colon with text after it. */
export const PREFIX_SHAPE = /^[A-Za-z][A-Za-z0-9._-]*:./
export function termProblem(term: string): boolean {
  // By code point, as the API tests a term (Unicode category Cc), since lint refuses a control character
  // in a regular expression.
  for (const character of Array.from(term)) {
    if (character === ',') return true
    const code = character.codePointAt(0) ?? 0
    if (code <= 0x1f || (code >= 0x7f && code <= 0x9f)) return true
  }
  return false
}

export type SeedOutcome =
  | { kind: 'idle' }
  | { kind: 'problem' }
  | { kind: 'searching' }
  | { kind: 'error'; error: unknown }   // error null: a body with no first result, or a candidate that is no entity
  | { kind: 'resolved'; candidate: GraphCandidate }
  | { kind: 'choose'; status: string; candidates: readonly GraphCandidate[]; truncated: boolean }
  | { kind: 'unmatched' }

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

/** What the page reads of a candidate's entity: its id, and `endName`'s label, type and, under a presentation's `node_label_namespace`, its primary identifier's value. */
function isEntity(value: unknown): boolean {
  if (!isObject(value)) return false
  const primary = value.primary_identifier
  return typeof value.id === 'string' && typeof value.label === 'string' && typeof value.type === 'string'
    && (!isObject(primary) || typeof primary.value === 'string')
}

function isCandidate(value: unknown): value is GraphCandidate {
  return isObject(value) && isEntity(value.entity)
}

export function seedOutcome(input: {
  problem: boolean; submitted: boolean; data: unknown; error: unknown; isFetching: boolean
}): SeedOutcome {
  if (input.problem) return { kind: 'problem' }
  if (!input.submitted) return { kind: 'idle' }
  if (input.isFetching && input.data === undefined) return { kind: 'searching' }
  if (input.error !== null && input.error !== undefined) return { kind: 'error', error: input.error }
  // One bad candidate refuses the whole answer, which would otherwise throw during render.
  const results = isObject(input.data) ? input.data.results : undefined
  const first: unknown = Array.isArray(results) ? results[0] : undefined
  if (!isObject(first) || typeof first.status !== 'string' || !Array.isArray(first.candidates)) return { kind: 'error', error: null }
  const candidates: unknown[] = first.candidates
  if (!candidates.every(isCandidate)) return { kind: 'error', error: null }
  const checked = candidates as GraphCandidate[]
  if (first.status === 'resolved' && checked.length > 0) return { kind: 'resolved', candidate: checked[0] }
  if (first.status === 'unmatched' || checked.length === 0) return { kind: 'unmatched' }
  // `candidate`, `ambiguous` or a status a deploy brings early: the user chooses, never an automatic pick.
  return { kind: 'choose', status: first.status, candidates: checked, truncated: first.truncated === true }
}

export function seedOutcomeText(outcome: SeedOutcome, context: {
  term: string; name: string | null; chosen: boolean; atLimit: boolean; max: number
  pending: boolean          // resolved, but this submit's add has neither reached `chosen` nor met the limit (8.2's `landed`)
  pickedName: string | null // the clicked candidate's name while `chosen` holds it (8.2's `picked`)
  organism: string | null   // what the sent `taxon` names (taxonName, 6.2); null when none was sent
}): string {
  switch (outcome.kind) {
    case 'idle':
      return ''
    case 'problem':
      return ONE_TERM
    case 'searching':
      return SEARCHING
    case 'error': {
      if (outcome.error === null) return `${SEARCH_FAILED} ${SEARCH_AGAIN}`
      const info = classifyGraphError(outcome.error)
      if (info.kind === 'throttled') return throttledText(info.retryAfter)
      if (info.kind === 'network') return `${SERVER_UNREACHABLE} ${SEARCH_AGAIN}`
      if (info.kind === 'timeout') return `${SEARCH_TIMEOUT} ${SEARCH_AGAIN}`
      // No invitation to search again where a repeat cannot change the answer, as F3's panels decide.
      if (!panelMayRetry(info)) return SEARCH_FAILED
      return `${SEARCH_FAILED} ${SEARCH_AGAIN}`
    }
    case 'resolved': {
      const name = context.name ?? ''
      if (context.chosen) return chosenText(name)
      if (context.atLimit) return limitText(context.max)
      if (context.pending) return SEARCHING
      return notChosenText(name)
    }
    case 'choose': {
      let text = outcome.status === 'candidate' ? candidateText(context.term) : ambiguousText(context.term)
      if (context.pickedName !== null) text = `${text} ${chosenText(context.pickedName)}`
      if (outcome.truncated) text = `${text} ${truncatedText(outcome.candidates.length)}`
      if (context.atLimit) text = `${text} ${limitText(context.max)}`
      return text
    }
    case 'unmatched':
      // The API reads a term of this shape as a prefix and a value, so a name written that way is unmatched.
      return PREFIX_SHAPE.test(context.term)
        ? `${unmatchedText(context.term, context.organism)} ${PREFIX_NOTE}`
        : unmatchedText(context.term, context.organism)
    default:
      return ''
  }
}
