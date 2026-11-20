import type { GraphBrowsersParams } from '../../api/graph/params'
import type { GraphAvailability, GraphInstanceStatus } from '../graph/types'

export const GALLERY_PARAM_NAMES = ['q', 'tag', 'relationship_type', 'taxon', 'assay', 'year', 'status', 'availability'] as const
export type GalleryParamName = (typeof GALLERY_PARAM_NAMES)[number]
export type GalleryFilterName = Exclude<GalleryParamName, 'q'>
export const GALLERY_FILTER_NAMES: readonly GalleryFilterName[] = ['tag', 'relationship_type', 'taxon', 'assay', 'year', 'status', 'availability']
export const Q_MAX_CHARS = 200        // backend/graph/api/limits.py; PAGE:1809
export const YEAR_MAX = 32_767        // backend/browsers/api.py:53-54; PAGE:1814
export interface GalleryAddress {
  params: GraphBrowsersParams                       // the values sent: valid ones only
  invalid: GalleryParamName[]                       // in GALLERY_PARAM_NAMES order
  shown: Partial<Record<GalleryParamName, string>>  // each present name's first raw value, for the controls
}

// By code point, as the API counts and tests them (Unicode category Cc), since lint refuses a control
// character in a regular expression.
function holdsControl(text: string): boolean {
  for (const character of Array.from(text)) {
    const code = character.codePointAt(0) ?? 0
    if (code <= 0x1f || (code >= 0x7f && code <= 0x9f)) return true
  }
  return false
}

function badText(text: string): boolean {
  return text === '' || Array.from(text).length > Q_MAX_CHARS || holdsControl(text)
}

// The digits are checked before the number, so Number('')'s 0 never goes out as `year=0`.
function positiveInteger(value: string): number | null {
  const number = Number(value)
  return /^[1-9][0-9]*$/.test(value) && Number.isSafeInteger(number) ? number : null
}

export function galleryAddress(search: URLSearchParams): GalleryAddress {
  // A value the API would refuse is named here and never sent, so the page can link to the address without
  // it (J16). The closed sets and the CURIE are the API's to judge; unknown names are never read.
  const params: GraphBrowsersParams = {}
  const invalid: GalleryParamName[] = []
  const shown: Partial<Record<GalleryParamName, string>> = {}
  for (const name of GALLERY_PARAM_NAMES) {
    const values = search.getAll(name)
    if (values.length === 0) continue
    shown[name] = values[0]
    if (values.length > 1) {
      invalid.push(name)
      continue
    }
    const value = values[0]
    if (name === 'q') {
      const trimmed = value.trim()
      if (badText(trimmed)) invalid.push(name)
      else params.q = trimmed
    } else if (name === 'taxon' || name === 'year') {
      const number = positiveInteger(value)
      if (number === null || (name === 'year' && number > YEAR_MAX)) invalid.push(name)
      else params[name] = number
    } else if (badText(value)) {
      invalid.push(name)
    } else if (name === 'status') {
      params.status = value as GraphInstanceStatus
    } else if (name === 'availability') {
      params.availability = value as GraphAvailability
    } else {
      params[name] = value
    }
  }
  return { params, invalid, shown }
}

export function withGalleryParam(search: URLSearchParams, name: GalleryParamName, value: string): URLSearchParams {
  // `set` replaces every occurrence of the name, so a repeated name leaves with one write.
  const next = new URLSearchParams(search)
  if (value === '') next.delete(name)
  else next.set(name, value)
  return next
}

export function withoutGalleryParams(search: URLSearchParams, names: readonly GalleryParamName[]): URLSearchParams {
  const next = new URLSearchParams(search)
  for (const name of names) next.delete(name)
  return next
}

export function searchString(search: URLSearchParams): string {
  const text = search.toString()
  return text === '' ? '' : `?${text}`
}

export function isSearched(address: GalleryAddress): boolean {
  return address.params.q !== undefined
}

export function isFiltered(address: GalleryAddress): boolean {
  return GALLERY_FILTER_NAMES.some((name) => address.params[name] !== undefined)
}

export interface FilterWrite { state: { filter: GalleryFilterName }; replace: boolean }
export function filterWrite(state: unknown, name: GalleryFilterName): FilterWrite {
  // Consecutive changes of one select replace one history entry, so Back returns to the address before the
  // first change, though a browser may fire a change on each arrow press in a closed select (J16).
  const replace = typeof state === 'object' && state !== null && (state as { filter?: unknown }).filter === name
  return { state: { filter: name }, replace }
}
