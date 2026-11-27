import type { GraphSelection } from '../../api/graph/params'
import type { GraphStatus } from '../graph/types'
import { EXPLORE_DEFAULT_VIEW } from '../graph/viewState'
import type { ExploreParamName, ExploreView } from '../graph/viewState'

/** The router state key naming the instance page an explorer view was opened from (Q2). */
export const EXPLORE_BACK_KEY = 'exploreBack'

/** The way back to an instance page: its address, the title its link names, and the instance page's own router state. */
export interface ExploreBack {
  pathname: string   // the instance's router path, `/browsers/<slug>`
  search: string     // '' or the instance address's search, '?seeds=…'
  title: string      // the instance's title as its page shows it, never ''
  state: unknown     // the instance page's router state, its own way back to the gallery, handed back on return
}

// B1's slug grammar (backend/browsers/rules.py:30-31), as the instance page checks it (BR/InstancePage.tsx:48).
const INSTANCE_PATH = /^\/browsers\/[a-z0-9]+(?:-[a-z0-9]+)*$/
const INSTANCE_PATH_MAX_CHARS = 60

export function exploreBackState(back: ExploreBack): Record<string, unknown> {
  return { [EXPLORE_BACK_KEY]: { pathname: back.pathname, search: back.search, title: back.title, state: back.state } }
}

export function exploreBackOf(state: unknown): ExploreBack | null {
  // Router state can hold anything a history entry kept, so only an instance path, a search and a title reach the link (J10).
  if (typeof state !== 'object' || state === null || Array.isArray(state)) return null
  const value: unknown = (state as Record<string, unknown>)[EXPLORE_BACK_KEY]
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return null
  const back = value as { pathname?: unknown; search?: unknown; title?: unknown; state?: unknown }
  if (typeof back.pathname !== 'string' || back.pathname.length > INSTANCE_PATH_MAX_CHARS || !INSTANCE_PATH.test(back.pathname)) return null
  if (typeof back.search !== 'string' || (back.search !== '' && !back.search.startsWith('?'))) return null
  if (typeof back.title !== 'string' || back.title === '') return null
  return { pathname: back.pathname, search: back.search, title: back.title, state: back.state }
}

/** The cap the notice words a closure's drop by, from status, never from a copy (Q1; A28): null when status gives none. */
export function expandedSeedLimit(status: GraphStatus | null | undefined): number | null {
  const value: unknown = status?.limits?.max_expanded_seeds
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= 1 ? value : null
}

/** The search's release selection (Q27): the view's browser, else its releases, else the default set. */
export function viewSelection(view: ExploreView): GraphSelection {
  if (view.browser !== null) return { browser: view.browser }
  if (view.releases !== null) return { releases: [...view.releases] }
  return {}
}

/** The view with each name a network 400 named back at its default, `follow` off (8.9); `v` and `selected` are no field of a view. */
export function withoutViewNames(view: ExploreView, names: readonly ExploreParamName[]): ExploreView {
  const next: ExploreView = { ...view }
  for (const name of names) {
    switch (name) {
      case 'seeds':
        next.seeds = EXPLORE_DEFAULT_VIEW.seeds
        break
      case 'follow':
        // A 400 naming expand refuses what the default sends, so the way out is off (8.9).
        next.follow = false
        break
      case 'outcome':
        next.allOutcomes = EXPLORE_DEFAULT_VIEW.allOutcomes
        break
      // One assignment per field: with strict off (frontend/tsconfig.json:14) a write through the union of these keys is `never`.
      case 'layers':
        next.layers = null
        break
      case 'direction':
        next.direction = null
        break
      case 'effect':
        next.effect = null
        break
      case 'evidence_basis':
        next.evidence_basis = null
        break
      case 'method':
        next.method = null
        break
      case 'score_types':
        next.score_types = null
        break
      case 'taxon':
        next.taxon = null
        break
      case 'releases':
        next.releases = null
        break
      case 'browser':
        next.browser = null
        break
      default:
        break
    }
  }
  return next
}
