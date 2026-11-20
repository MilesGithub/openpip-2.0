import { own } from '../graph/network/graph'
import { plural, slugWords } from '../graph/network/words'
import type {
  GraphAvailability, GraphInstanceKind, GraphInstanceStatus, GraphInstanceVisibility, GraphSelectionRole,
} from '../graph/types'
import { isolateText } from '../graph/words/isolateText'
import type { GalleryFilterName, GalleryParamName } from './galleryAddress'

// Constants in a strict module rather than a text group, so the views stay pure functions (J31). A word
// map over a closed union is read through `own` with a slug fallback, so a code a deploy brings early
// reads as its slug's words and nothing prints "undefined" or a prototype member (J42).

export const SEARCH_FORM_NAME = 'Search the browsers'
/** Never "title" alone: the API's `q` also searches the short title and the summary. */
export const SEARCH_LABEL = 'Search titles and summaries'
export const SEARCH_BUTTON = 'Search'
export const SEARCH_CLEAR = 'Clear search'
export const FILTERS_NAME = 'Filters'
/**
 * `status` filters the stored state an admin sets, hence "Stated status": "Available" can then sit beside a
 * card that reads "Data unavailable" without seeming to contradict it (J18).
 */
export const FILTER_LABELS: Readonly<Record<GalleryFilterName, string>> = {
  tag: 'Biology area',
  relationship_type: 'Relationship type',
  taxon: 'Organism',
  assay: 'Assay',
  year: 'Publication year',
  status: 'Stated status',
  availability: 'Data availability',
}
export const ANY_OPTION: Readonly<Record<GalleryFilterName, string>> = {
  tag: 'Any biology area',
  relationship_type: 'Any relationship type',
  taxon: 'Any organism',
  assay: 'Any assay',
  year: 'Any year',
  status: 'Any stated status',
  availability: 'Any availability',
}
export const CLEAR_FILTERS = 'Clear filters'
export const LOADING_BROWSERS = 'Loading browsers…'

export function countText(n: number): string {
  return plural(n, '1 browser listed.', '{n} browsers listed.')
}

/** Never "not enabled", and never a claim about other portals (J19). */
export const EMPTY_LIST = 'No browser is listed here for you.'

export function noMatchText(searched: boolean, filtered: boolean): string {
  if (searched && filtered) return 'No browser matches this search and these filters.'
  return searched ? 'No browser matches this search.' : 'No browser matches these filters.'
}

export const INCOMPLETE_LIST = 'This list may be incomplete: the server sent only its first page.'
export const LIST_ERROR = 'The browsers could not be loaded.'
export const LIST_TIMEOUT = 'The browsers did not arrive in time.'
export const PARAM_NOUNS: Readonly<Record<GalleryParamName, string>> = {
  q: 'search',
  tag: 'biology area',
  relationship_type: 'relationship type',
  taxon: 'organism',
  assay: 'assay',
  year: 'publication year',
  status: 'status',
  availability: 'availability',
}

function joinWords(words: readonly string[]): string {
  if (words.length <= 1) return words.join('')
  return `${words.slice(0, -1).join(', ')} and ${words[words.length - 1]}`
}

export function paramErrorText(names: readonly GalleryParamName[]): string {
  if (names.length === 0) return 'The gallery cannot use this address.'
  return `The gallery cannot use the ${joinWords(names.map((name) => PARAM_NOUNS[name]))} in this address.`
}

/** The link names what it drops, so its text alone says its purpose. */
export function removalText(names: readonly GalleryParamName[]): string {
  if (names.length === 0) return 'Show the whole gallery'
  return `Show the gallery without the ${joinWords(names.map((name) => PARAM_NOUNS[name]))}`
}

export const KIND_WORDS: Readonly<Record<GraphInstanceKind, string>> = {
  typed: 'Typed data',
  portal_ppi: "This portal's search",
  external: 'External portal',
  metadata_only: 'Citation card',
}
export const AVAILABILITY_WORDS: Readonly<Record<GraphAvailability, string>> = {
  live: 'Live',
  planned: 'Planned',
  external: 'External',
  metadata_only: 'Data unavailable',
  retired: 'Retired',
}
export const STATUS_WORDS: Readonly<Record<GraphInstanceStatus, string>> = {
  planned: 'Planned',
  available: 'Available',
  retired: 'Retired',
}
/** One option covers both meanings of `metadata_only`: a citation card, and a typed instance with no data. */
export const AVAILABILITY_OPTION_WORDS: Readonly<Record<GraphAvailability, string>> = {
  live: 'Live',
  planned: 'Planned',
  external: 'External portal',
  metadata_only: 'Citation card or data unavailable',
  retired: 'Retired',
}
export const VISIBILITY_WORDS: Readonly<Record<Exclude<GraphInstanceVisibility, 'public'>, string>> = {
  staff: 'Staff only',
  unlisted: 'Unlisted',
}
export const ROLE_WORDS: Readonly<Record<GraphSelectionRole, string>> = {
  primary: "the study's own release",
  context: 'a release that supports it',
}
/** The tag list is open, so the map is keyed by string; a tag a later row adds takes its words here. */
export const TAG_WORDS: Readonly<Record<string, string>> = {
  protein_interaction: 'Protein interactions',
  gene_regulation: 'Gene regulation',
  rna_regulation: 'RNA regulation',
  genetic_interaction: 'Genetic interactions',
  signaling: 'Signalling',
  ligand_receptor: 'Ligands and receptors',
  protein_modification: 'Protein modification',
  disease: 'Disease',
  host_pathogen: 'Hosts and pathogens',
  phenotype: 'Phenotypes',
}
export const CITATION_CARD_NO_DATA = 'No data'

export function kindWords(kind: string): string {
  return own(KIND_WORDS, kind) ?? slugWords(kind)
}

export function availabilityWords(availability: string): string {
  return own(AVAILABILITY_WORDS, availability) ?? slugWords(availability)
}

export function statusWords(status: string): string {
  return own(STATUS_WORDS, status) ?? slugWords(status)
}

export function availabilityOptionWords(availability: string): string {
  return own(AVAILABILITY_OPTION_WORDS, availability) ?? slugWords(availability)
}

export function tagWords(tag: string): string {
  return own(TAG_WORDS, tag) ?? slugWords(tag)
}

export function roleWords(role: string): string {
  return own(ROLE_WORDS, role) ?? slugWords(role)
}

export function visibilityWords(visibility: string): string | null {
  if (visibility === 'public') return null
  return own(VISIBILITY_WORDS, visibility) ?? slugWords(visibility)
}

/**
 * Every pair the API's availability rules give reads differently, so planned, external, metadata-only,
 * retired and live entries look different in words; a typed instance with no data never reads "Citation card".
 */
export function stateWords(kind: string, availability: string): string {
  if (availability === 'live' && kind === 'portal_ppi') return "This portal's search"
  if (availability === 'external' && kind === 'external') return 'External portal'
  if (availability === 'metadata_only' && kind === 'metadata_only') return `${kindWords(kind)} · ${CITATION_CARD_NO_DATA}`
  return `${kindWords(kind)} · ${availabilityWords(availability)}`
}

export const CARD_TERMS = {
  cited: 'Cited',
  relationshipTypes: 'Relationship types',
  organisms: 'Organisms',
  tags: 'Biology areas',
} as const
export const PORTAL_SEARCH_ACTION = "Open this portal's search"
export const EXTERNAL_ACTION = 'External portal at'

export const LOADING_BROWSER = 'Loading this browser…'
/** Every `not_found` reads alike, since an absent instance, one the caller may not see and a switched-off route answer alike: never "does not exist". */
export const BROWSER_NOT_AVAILABLE = 'This browser is not available to you.'
export const BROWSER_ERROR = 'This browser could not be loaded.'
export const BROWSER_TIMEOUT = 'This browser did not arrive in time.'
/** Never "All browsers": the link keeps the last gallery search (J10), so the gallery it opens may be filtered. */
export const BACK_LINK = 'Back to the gallery'
export const HEADER_HEADINGS = {
  description: 'Description',
  methods: 'Methods',
  citation: 'Citation',
  organisms: 'Organisms',
  releases: 'Data releases',
} as const
/** An empty selection can mean the caller may see none of it, so this never says there is none (J24). */
export const NO_RELEASE = 'No release of this browser is available to you.'

export function versionLine(releaseText: string, role: string): string {
  return `${isolateText(releaseText)}: ${roleWords(role)}`
}

// Every view reads a body's nested lists through these two, so an item of the wrong type is skipped, never
// shown or counted, where reading it would throw during render. They live here because every view imports
// this module and it imports no view, so no import cycle forms.

/** The items that are objects, neither null nor arrays; [] for a value that is not an array. */
export function objectItems<T extends object>(list: readonly T[] | null | undefined): T[] {
  if (!Array.isArray(list)) return []
  return list.filter((item: unknown): item is T => typeof item === 'object' && item !== null && !Array.isArray(item))
}

/** The items that are strings, typed as the list's own items; [] for a value that is not an array. */
export function stringItems<T extends string>(list: readonly T[] | null | undefined): T[] {
  if (!Array.isArray(list)) return []
  return list.filter((item: unknown): item is T => typeof item === 'string')
}
