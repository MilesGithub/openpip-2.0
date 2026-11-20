import { classifyGraphError } from '../../api/graph/errors'
import { routeErrorView } from '../graph/pages/routeState'
import type { GraphBrowsersResponse, GraphInstanceItem } from '../graph/types'
import {
  EMPTY_LIST, INCOMPLETE_LIST, LIST_ERROR, LIST_TIMEOUT, countText, noMatchText, paramErrorText,
} from './browserWords'
import { GALLERY_PARAM_NAMES, isFiltered, isSearched } from './galleryAddress'
import type { GalleryAddress, GalleryParamName } from './galleryAddress'

// The fields a card reads as strings: one item without them, or a null item, would throw during render and
// the route's boundary would blank the page, the filtered list with it.
const ITEM_STRINGS = ['slug', 'title', 'kind', 'status', 'availability', 'visibility', 'summary'] as const

function isItem(value: unknown): boolean {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return false
  const item = value as Record<string, unknown>
  return ITEM_STRINGS.every((key) => typeof item[key] === 'string')
}

export function isGalleryBody(data: unknown): data is GraphBrowsersResponse {   // instances an array of objects with seven string fields
  if (typeof data !== 'object' || data === null || Array.isArray(data)) return false
  const instances = (data as { instances?: unknown }).instances
  return Array.isArray(instances) && instances.every(isItem)
}

export interface GalleryListQuery { data: unknown; error: unknown; isPlaceholderData: boolean }
export interface GalleryView {
  line: string                                      // the count line, TypedRouteStatus's text
  loading: boolean                                  // LOADING_BROWSERS in the list area
  instances: readonly GraphInstanceItem[] | null    // null: no list
  removal: readonly GalleryParamName[] | null       // the names the removal link drops; [] drops all eight
  retry: boolean                                    // the row offers "Try again"
}

function view(line: string, given: Partial<GalleryView> = {}): GalleryView {
  return { line, loading: false, instances: null, removal: null, retry: false, ...given }
}

export function galleryView(address: GalleryAddress, query: GalleryListQuery): GalleryView {
  // No list request goes out for an invalid address, so whatever placeholder `data` holds shows no list (J16).
  if (address.invalid.length > 0) return view(paramErrorText(address.invalid), { removal: address.invalid })
  if (query.error !== null && query.error !== undefined) {
    const info = classifyGraphError(query.error)
    if (info.kind === 'bad_request') {
      // Named from `errors[].parameter` only, never from `detail`.
      const named = new Set(info.errors.map((problem) => problem?.parameter))
      const names = GALLERY_PARAM_NAMES.filter((name) => named.has(name))
      return view(paramErrorText(names), { removal: names })
    }
    // F1 refused to send a line too long for a server, or a web server refused it: a code point can take 12
    // characters percent-encoded, so the caps on each value cannot rule this out. No name is at fault.
    if (info.kind === 'too_long' || info.kind === 'server_refused') return view(paramErrorText([]), { removal: [] })
    const failure = routeErrorView(query.error, { other: LIST_ERROR, notFound: null, timeout: LIST_TIMEOUT })
    return view(failure.text, { retry: failure.retry })
  }
  if (query.data === undefined) return view('', { loading: true })
  if (!isGalleryBody(query.data)) return view(LIST_ERROR, { retry: true })
  const body = query.data
  // The last list stays while new filters load.
  if (query.isPlaceholderData) return view('', { loading: true, instances: body.instances })
  // The API's order, never re-sorted; a further page is named, never fetched (J19).
  const n = body.instances.length
  const searched = isSearched(address)
  const filtered = isFiltered(address)
  const counted = n > 0 ? countText(n) : searched || filtered ? noMatchText(searched, filtered) : EMPTY_LIST
  const line = typeof body.page?.next === 'string' ? `${counted} ${INCOMPLETE_LIST}` : counted
  return view(line, { instances: body.instances })
}
