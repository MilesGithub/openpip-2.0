import { clipText } from '../graph/panels/text'
import type { ExploreParamName } from '../graph/viewState'
import { isolateText } from '../graph/words/isolateText'

export const SEARCH_HEADING = 'Find entities'
export const SEARCH_UNAVAILABLE = 'The entity search is not available on this portal; the entities chosen for this view are still drawn.'
export const SEARCH_UNAVAILABLE_EMPTY = 'The entity search is not available on this portal, and this view names no entity to draw.'
export const NO_SEED_PROMPT = 'Search for one or more entities to draw their network.'
export const NETWORK_HEADING = 'Network'
export const CHOSEN_LABEL = 'Chosen entities'

export function removeText(name: string): string {
  return `Remove ${isolateText(name)}`
}

export const REMOVE_ALL = 'Remove all chosen entities'

export function backText(title: string): string {
  return `Back to ${isolateText(clipText(title))}`
}

export const NEWER_VERSION = 'This view needs a newer version of openPIP.'
export const UNREADABLE_VERSION = 'The explorer cannot read the version this address names.'
export const NEWER_VERSION_LINK = 'Open the explorer with no entities chosen'

export const EXPLORE_NAME_NOUNS: Readonly<Record<ExploreParamName, string>> = {
  v: 'version',
  seeds: 'chosen entities',
  follow: 'gene-to-protein setting',
  outcome: 'tested-absent setting',
  layers: 'layers',
  direction: 'direction',
  effect: 'effects',
  evidence_basis: 'evidence bases',
  method: 'detection methods',
  score_types: 'score types',
  taxon: 'partner organism',
  releases: 'releases',
  browser: 'browser',
  selected: 'selection',
}

function joinWords(words: readonly string[]): string {
  if (words.length <= 1) return words.join('')
  return `${words.slice(0, -1).join(', ')} and ${words[words.length - 1]}`
}

function nouns(names: readonly ExploreParamName[]): string[] {
  return names.map((name) => EXPLORE_NAME_NOUNS[name])
}

export function addressProblemText(names: readonly ExploreParamName[]): string {
  if (names.length === 0) return 'The explorer cannot use this address.'
  return `The explorer cannot use the ${joinWords(nouns(names))} in this address.`
}

export function addressRemovalText(names: readonly ExploreParamName[]): string {
  if (names.length === 0) return NEWER_VERSION_LINK
  return `Show the explorer without the ${joinWords(nouns(names))}`
}

export const VIEW_PROBLEM = 'The network cannot use this view.'
export const FOLLOW_COST =
  'Following gene-to-protein links adds the entities they reach to the lookups; turn it off to ask for a smaller network.'
export const NETWORK_AGAIN = 'Press Try again to ask for this network again.'

export function viewProblemText(names: readonly ExploreParamName[]): string {
  if (names.length === 0) return VIEW_PROBLEM
  return `The network cannot use the ${joinWords(nouns(names))} of this view.`
}

export function viewRemovalText(names: readonly ExploreParamName[]): string {
  if (names.length === 0) return 'Remove the values from this view'
  return `Remove the ${joinWords(nouns(names))} from this view`
}
