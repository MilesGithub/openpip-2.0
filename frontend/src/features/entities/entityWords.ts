import { plural } from '../graph/network/words'
import { clipText } from '../graph/panels/text'
import { isolateText } from '../graph/words/isolateText'
import type { EntitySelectionName } from './entityAddress'

function shown(value: string): string {
  return isolateText(clipText(value))
}

export const ENTITY_ADDRESS_PROBLEM = "This address names no entity: an entity page's address ends with entity: and a number, such as entity:7."
export const EXPLORE_ENTITY = 'Explore this entity in the unified network'
export const SELECTION_NOUNS: Readonly<Record<EntitySelectionName, string>> = { releases: 'releases', browser: 'browser' }

function joinWords(words: readonly string[]): string {
  if (words.length <= 1) return words.join('')
  return `${words.slice(0, -1).join(', ')} and ${words[words.length - 1]}`
}

export function selectionProblemText(names: readonly EntitySelectionName[]): string {
  if (names.length === 0) return 'This page cannot use this address.'
  return `This page cannot use the ${joinWords(names.map((name) => SELECTION_NOUNS[name]))} named in this address.`
}

export function selectionRemovalText(names: readonly EntitySelectionName[]): string {
  if (names.length === 0) return 'Show this page'
  return `Show this page without the ${joinWords(names.map((name) => SELECTION_NOUNS[name]))}`
}

export const LOOKUP_SEARCHING = 'Finding the entity…'
export const LOOKUP_CANDIDATES_NAME = 'Matching entities'

export function openText(name: string): string {
  return `Open ${isolateText(name)}`
}

export function lookupSubjectText(label: string | null, namespace: string, value: string): string {
  return label !== null ? `${label} “${shown(value)}”` : `“${shown(value)}” in the namespace “${isolateText(clipText(namespace))}”`
}

export function lookupCandidateText(value: string): string {
  return `“${shown(value)}” matches one entity, though not as its primary identifier is written. Open it if it is the one you meant.`
}

export function lookupAmbiguousText(value: string): string {
  return `“${shown(value)}” matches more than one entity. Open the one you meant.`
}

export function lookupTruncatedText(listed: number): string {
  return `More entities match than the ${listed} listed.`
}

export function chooseText(status: string, value: string, listed: number | null): string {
  const first = status === 'candidate' ? lookupCandidateText(value) : lookupAmbiguousText(value)
  return listed === null ? first : `${first} ${lookupTruncatedText(listed)}`
}

export function lookupUnmatchedText(value: string, label: string | null, scoped: boolean): string {
  const scope = scoped ? ' in the releases this address names' : ''
  return label !== null
    ? `No entity available to you${scope} has the ${label} “${shown(value)}”.`
    : `No entity available to you${scope} has the identifier “${shown(value)}” in this namespace.`
}

export function separatorValueText(value: string): string {
  return `“${shown(value)}” holds a comma, a tab or a line break, which would make it more than one identifier, so it was not looked up.`
}

export function controlValueText(value: string): string {
  return `“${shown(value)}” holds a control character, so it was not looked up.`
}

export function longValueText(value: string, max: number): string {
  return `“${shown(value)}” is longer than ${plural(max, '1 character', '{n} characters')}, so it was not looked up.`
}

export const EMPTY_VALUE = 'This address holds no identifier to look up.'
export const NAMESPACE_PROBLEM = 'This portal uses no identifier namespace of that name.'
export const VALUE_PROBLEM = 'The server refused to look up this identifier.'
export const LOOKUP_PROBLEM = 'The server refused this address.'

export function lookupProblemText(problems: readonly string[]): string {
  const parts: string[] = []
  if (problems.includes('namespace')) parts.push(NAMESPACE_PROBLEM)
  if (problems.includes('q')) parts.push(VALUE_PROBLEM)
  const selection = (['releases', 'browser'] as const).filter((name) => problems.includes(name))
  if (selection.length > 0) parts.push(selectionProblemText(selection))
  if (!['namespace', 'q', 'releases', 'browser'].some((name) => problems.includes(name))) parts.push(LOOKUP_PROBLEM)
  return parts.join(' ')
}

export const LOOKUP_ERROR = 'This identifier could not be looked up.'
export const LOOKUP_TIMEOUT = 'The answer for this identifier did not arrive in time.'
