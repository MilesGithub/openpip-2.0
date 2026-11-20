import type { GraphEntityRef } from '../graph/types'

export const SEEDS_PARAM = 'seeds'
export const SEED_LIMIT_FALLBACK = 10   // MAX_SEEDS, backend/graph/api/limits.py:47; FX/status.ts:37
export interface SeedsAddress { seeds: GraphEntityRef[]; invalid: boolean }

// The API's ref grammar (backend/graph/api/refs.py) less `entity:0`, which names nothing: a ref of 20 or
// more digits, which the API answers with a 400, is malformed here and sends nothing. The form is checked,
// never the number's meaning, since refs stay opaque (J1).
const SEED_REF = /^entity:[1-9][0-9]{0,18}$/

export function seedsFromSearch(search: URLSearchParams, max: number): SeedsAddress {
  const values = search.getAll(SEEDS_PARAM)
  if (values.length === 0) return { seeds: [], invalid: false }
  if (values.length > 1 || values[0] === '') return { seeds: [], invalid: true }
  const refs = values[0].split(',')
  const malformed = refs.some((ref) => !SEED_REF.test(ref))
  if (malformed || new Set(refs).size !== refs.length || refs.length > max) return { seeds: [], invalid: true }
  return { seeds: refs, invalid: false }
}

export function withSeeds(search: URLSearchParams, seeds: readonly GraphEntityRef[]): URLSearchParams {
  const next = new URLSearchParams(search)
  if (seeds.length === 0) next.delete(SEEDS_PARAM)
  else next.set(SEEDS_PARAM, seeds.join(','))
  return next
}

export function instanceHref(slug: string, seeds?: readonly GraphEntityRef[]): string {
  // A router path, so the router adds App.tsx's basename; never a full address.
  const path = `/browsers/${encodeURIComponent(slug)}`
  return seeds !== undefined && seeds.length > 0 ? `${path}?${withSeeds(new URLSearchParams(), seeds)}` : path
}

export interface GalleryLinkState { gallerySearch: string }
export function galleryLinkState(search: string): GalleryLinkState {
  return { gallerySearch: search }
}

export function galleryBackTo(state: unknown): { pathname: string; search: string } {
  // Router state can hold anything a history entry kept, so only a search string reaches the link (J10).
  const value = typeof state === 'object' && state !== null ? (state as { gallerySearch?: unknown }).gallerySearch : undefined
  const search = typeof value === 'string' && (value === '' || value.startsWith('?')) ? value : ''
  return { pathname: '/browsers', search }
}
