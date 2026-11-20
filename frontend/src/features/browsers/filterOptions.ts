import { typeLabel } from '../graph/network/words'
import { termText } from '../graph/panels/panelWords'
import { clipText } from '../graph/panels/text'
import type { GraphTermRef, GraphVocabularies } from '../graph/types'
import { organismTaxids, taxonWords } from '../graph/words/taxa'
import { availabilityOptionWords, objectItems, statusWords, stringItems, tagWords } from './browserWords'
import type { GalleryFilterName, GalleryParamName } from './galleryAddress'
import { GALLERY_FILTER_NAMES } from './galleryAddress'
import { isGalleryBody } from './galleryView'

export interface FilterOption { value: string; text: string }
export type FilterOptions = Readonly<Record<GalleryFilterName, readonly FilterOption[]>>

function sortedStrings(values: Iterable<string>): string[] {
  return Array.from(new Set(values)).sort()
}

function sortedNumbers(values: Iterable<number>, descending: boolean): string[] {
  return Array.from(new Set(values)).sort((a, b) => (descending ? b - a : a - b)).map((value) => String(value))
}

function appendedText(name: GalleryFilterName, value: string, vocabularies: GraphVocabularies | null | undefined): string {
  switch (name) {
    case 'tag':
      return tagWords(value)
    case 'relationship_type':
      return typeLabel(value, vocabularies)
    case 'taxon':
      return /^[1-9][0-9]*$/.test(value) ? taxonWords(Number(value), vocabularies) : value
    case 'status':
      return statusWords(value)
    case 'availability':
      return availabilityOptionWords(value)
    default:
      return value
  }
}

export function galleryFilterOptions(
  body: unknown,
  vocabularies: GraphVocabularies | null | undefined,
  shown: Partial<Record<GalleryParamName, string>>,
): FilterOptions {
  // No endpoint names the tags, kinds, statuses or availabilities, so the options are the union over the
  // unfiltered list. A body that fails the gallery's guard gives none, beside a filtered list that is sound.
  const instances = isGalleryBody(body) ? body.instances : []
  const tags: string[] = []
  const types: string[] = []
  const taxa: number[] = []
  const assays = new Map<string, GraphTermRef>()
  const years: number[] = []
  const statuses: string[] = []
  const availabilities: string[] = []
  for (const item of instances) {
    tags.push(...stringItems(item.tags))
    types.push(...stringItems(item.relationship_types))
    // The filter matches the primary taxon or any of `taxa`, so both are offered.
    taxa.push(...organismTaxids(item.taxa, item.primary_taxon))
    for (const assay of objectItems(item.assays)) if (!assays.has(assay.curie)) assays.set(assay.curie, assay)
    for (const publication of objectItems(item.publications)) if (publication.year !== null) years.push(publication.year)
    statuses.push(item.status)
    availabilities.push(item.availability)
  }
  const options: Record<GalleryFilterName, FilterOption[]> = {
    tag: sortedStrings(tags).map((value) => ({ value, text: tagWords(value) })),
    relationship_type: sortedStrings(types).map((value) => ({ value, text: typeLabel(value, vocabularies) })),
    taxon: sortedNumbers(taxa, false).map((value) => ({ value, text: taxonWords(Number(value), vocabularies) })),
    assay: sortedStrings(assays.keys()).map((value) => ({ value, text: termText(assays.get(value) as GraphTermRef) })),
    year: sortedNumbers(years, true).map((value) => ({ value, text: value })),
    status: sortedStrings(statuses).map((value) => ({ value, text: statusWords(value) })),
    availability: sortedStrings(availabilities).map((value) => ({ value, text: availabilityOptionWords(value) })),
  }
  // A select always holds the address's value, even one the list lacks or the API refuses.
  for (const name of GALLERY_FILTER_NAMES) {
    const value = shown[name]
    if (typeof value === 'string' && value !== '' && !options[name].some((option) => option.value === value)) {
      options[name].push({ value, text: appendedText(name, value, vocabularies) })
    }
  }
  // Every option text is clipped, the address's included, so a 5,000-character value cannot fill a select.
  for (const name of GALLERY_FILTER_NAMES) {
    options[name] = options[name].map((option) => ({ value: option.value, text: clipText(option.text) }))
  }
  return options
}
