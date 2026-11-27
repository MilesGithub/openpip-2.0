import type { GraphSelection } from '../../api/graph/params'
import type { GraphEntityRef } from '../graph/types'
import { exploreValueValid } from '../graph/viewState'
import type { ExploreLimits } from '../graph/viewState'

/** The entity pages' path (Q22): `/entities/:id` and `/entities/:namespace/:value` sit under it. */
export const ENTITIES_PATH = '/entities'

// F4's ref grammar (BR/instanceAddress.ts:7-10): the API's (backend/graph/api/refs.py:17-20) less entity:0, which names
// nothing, so a ref the API would refuse is never sent. The form is checked, never the number's meaning (J1). L2 extends
// it here, in viewState.ts and in instanceAddress.ts, the three copies a test pins equal (PLAN:204).
const ENTITY_REF = /^entity:[1-9][0-9]{0,18}$/

export function isEntityRef(value: string): boolean {
  return ENTITY_REF.test(value)
}

export type EntitySelectionName = 'releases' | 'browser'
/** The two address names both entity pages read (SD28), in the order a problem names them. */
export const ENTITY_SELECTION_NAMES: readonly EntitySelectionName[] = ['releases', 'browser']
export interface EntitySelectionAddress {
  selection: GraphSelection          // {}, { releases } or { browser }, never both: the requests' selection
  invalid: EntitySelectionName[]     // malformed, repeated or given together, in ENTITY_SELECTION_NAMES order
}

export function entitySelectionFromSearch(search: URLSearchParams, limits: ExploreLimits): EntitySelectionAddress {
  // J16, by the explorer's own grammar for these two names (viewState.ts): each read once, a repeat or a malformed value
  // named and sent nowhere, both together named, since the API refuses them together (backend/graph/api/base.py:82-93).
  const releases = search.getAll('releases')
  const browser = search.getAll('browser')
  if (releases.length > 0 && browser.length > 0) return { selection: {}, invalid: ['releases', 'browser'] }
  if (releases.length > 1 || (releases.length === 1 && !exploreValueValid('releases', releases[0], limits))) {
    return { selection: {}, invalid: ['releases'] }
  }
  if (browser.length > 1 || (browser.length === 1 && !exploreValueValid('browser', browser[0], limits))) {
    return { selection: {}, invalid: ['browser'] }
  }
  if (releases.length === 1) return { selection: { releases: releases[0].split(',').sort() }, invalid: [] }
  if (browser.length === 1) return { selection: { browser: browser[0] }, invalid: [] }
  return { selection: {}, invalid: [] }
}

/** '' or the selection as a search, `?releases=…` or `?browser=…`, as URLSearchParams writes it. */
export function entitySelectionSearch(selection: GraphSelection): string {
  const search = new URLSearchParams()
  if (typeof selection.browser === 'string' && selection.browser !== '') search.set('browser', selection.browser)
  else if (Array.isArray(selection.releases) && selection.releases.length > 0) {
    search.set('releases', [...new Set(selection.releases)].sort().join(','))
  }
  const text = search.toString()
  return text === '' ? '' : `?${text}`
}

export function entityHref(ref: GraphEntityRef, selection: GraphSelection = {}): string {
  // A router path, so the router adds App.tsx's basename; never a full address (BR/instanceAddress.ts:29-33).
  return `${ENTITIES_PATH}/${encodeURIComponent(ref)}${entitySelectionSearch(selection)}`
}

export function entityLookupHref(namespace: string, value: string, selection: GraphSelection = {}): string {
  return `${ENTITIES_PATH}/${encodeURIComponent(namespace)}/${encodeURIComponent(value)}${entitySelectionSearch(selection)}`
}
