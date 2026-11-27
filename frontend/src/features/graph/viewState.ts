import type { GraphNetworkParams, GraphSelection } from '../../api/graph/params'
import type { TypedSelection } from './network/graph'
import type {
  GraphDirection, GraphEffectKey, GraphEntityRef, GraphEvidenceBasis, GraphRelationshipRef, GraphReleaseRef, GraphStatus,
} from './types'

export const EXPLORE_PATH = '/explore'
export const EXPLORE_VIEW_VERSION = 1
/** The address names, in the order a write sets them (Q26). */
export const EXPLORE_PARAM_NAMES = [
  'v', 'seeds', 'follow', 'outcome', 'layers', 'direction', 'effect', 'evidence_basis', 'method', 'score_types', 'taxon',
  'releases', 'browser', 'selected',
] as const
export type ExploreParamName = (typeof EXPLORE_PARAM_NAMES)[number]

/**
 * One explorer view as plain JSON, which S1 saves with a `kind`. Each list is distinct and in plain string order, but
 * `seeds`, which keep the order they were chosen in; null is absent, never an empty list.
 */
export interface ExploreView {
  v: typeof EXPLORE_VIEW_VERSION
  seeds: readonly GraphEntityRef[]
  follow: boolean
  allOutcomes: boolean
  layers: readonly string[] | null
  direction: GraphDirection | null
  effect: readonly GraphEffectKey[] | null
  evidence_basis: readonly string[] | null
  method: readonly string[] | null
  score_types: readonly string[] | null
  taxon: number | null
  releases: readonly GraphReleaseRef[] | null
  browser: string | null
}
export const EXPLORE_DEFAULT_VIEW: ExploreView = {
  v: 1, seeds: [], follow: true, allOutcomes: false, layers: null, direction: null, effect: null, evidence_basis: null,
  method: null, score_types: null, taxon: null, releases: null, browser: null,
}

export interface ExploreLimits { maxSeeds: number; maxReleases: number; maxLayers: number }
export const EXPLORE_LIMIT_FALLBACKS: ExploreLimits = { maxSeeds: 10, maxReleases: 50, maxLayers: 20 }

function whole(value: unknown, fallback: number): number {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= 1 ? value : fallback
}

export function exploreLimits(status: GraphStatus | null | undefined): ExploreLimits {
  const limits = status?.limits
  return {
    maxSeeds: whole(limits?.max_seeds, EXPLORE_LIMIT_FALLBACKS.maxSeeds),
    maxReleases: whole(limits?.max_releases, EXPLORE_LIMIT_FALLBACKS.maxReleases),
    maxLayers: whole(limits?.max_layers, EXPLORE_LIMIT_FALLBACKS.maxLayers),
  }
}

export type ExploreSelectedRef = GraphEntityRef | GraphRelationshipRef
export interface ExploreAddress {
  version: typeof EXPLORE_VIEW_VERSION | 'other' | 'unreadable'
  view: ExploreView                    // the valid values; an absent or invalid name keeps EXPLORE_DEFAULT_VIEW's value
  selected: ExploreSelectedRef | null  // URL-only: never part of the view (PLAN:190 From F3)
  invalid: ExploreParamName[]          // malformed or repeated, in EXPLORE_PARAM_NAMES order, each once
  unknown: string[]                    // names outside EXPLORE_PARAM_NAMES, in first-appearance order, each once
}

const ENTITY_REF = /^entity:[1-9][0-9]{0,18}$/
const RELATIONSHIP_REF = /^relationship:[1-9][0-9]{0,18}$/
const RELEASE_REF = /^release:[1-9][0-9]{0,18}$/
const LAYER_PIECE = /^(?:family:)?[a-z][a-z0-9_]{0,49}$/
const BASIS_PIECE = /^[a-z][a-z0-9_]{0,49}$/
const METHOD_CURIE = /^MI:[0-9]{4}$/
const SCORE_SLUG = /^[a-z][a-z0-9_]*:[a-z0-9][a-z0-9_.-]*$/
const SCORE_SLUG_MAX_CHARS = 200
const WHOLE = /^[1-9][0-9]*$/
const BROWSER_SLUG = /^[a-z0-9]+(?:-[a-z0-9]+)*$/
const BROWSER_SLUG_MAX_CHARS = 50
const DIRECTIONS: Readonly<Record<GraphDirection, true>> = { out: true, in: true, both: true }
const EFFECT_KEYS: Readonly<Record<GraphEffectKey, true>> = { activating: true, inhibiting: true, unknown: true, not_stated: true }

function hasKey(record: Readonly<Record<string, true>>, key: string): boolean {
  return Object.prototype.hasOwnProperty.call(record, key)
}

/** Every piece passes, none repeats, and at most `max` of them; an empty value or piece is malformed. */
function pieces(value: string, test: (piece: string) => boolean, max = Number.MAX_SAFE_INTEGER): boolean {
  if (value === '') return false
  const parts = value.split(',')
  return parts.every(test) && new Set(parts).size === parts.length && parts.length <= max
}

function isKnown(name: string): name is ExploreParamName {
  return (EXPLORE_PARAM_NAMES as readonly string[]).indexOf(name) !== -1
}

export function exploreValueValid(name: Exclude<ExploreParamName, 'v'>, value: string, limits: ExploreLimits): boolean {
  switch (name) {
    case 'seeds':
      return pieces(value, (piece) => ENTITY_REF.test(piece), limits.maxSeeds)
    case 'follow':
      return value === 'off'
    case 'outcome':
      return value === 'all'
    case 'layers':
      return pieces(value, (piece) => LAYER_PIECE.test(piece), limits.maxLayers)
    case 'direction':
      return hasKey(DIRECTIONS, value)
    case 'effect':
      return pieces(value, (piece) => hasKey(EFFECT_KEYS, piece))
    case 'evidence_basis':
      return pieces(value, (piece) => BASIS_PIECE.test(piece))
    case 'method':
      return pieces(value, (piece) => METHOD_CURIE.test(piece))
    case 'score_types':
      return pieces(value, (piece) => SCORE_SLUG.test(piece) && piece.length <= SCORE_SLUG_MAX_CHARS)
    case 'taxon':
      return WHOLE.test(value) && Number.isSafeInteger(Number(value))
    case 'releases':
      return pieces(value, (piece) => RELEASE_REF.test(piece), limits.maxReleases)
    case 'browser':
      return BROWSER_SLUG.test(value) && value.length <= BROWSER_SLUG_MAX_CHARS
    case 'selected':
      return ENTITY_REF.test(value) || RELATIONSHIP_REF.test(value)
    default:
      return false
  }
}

const sorted = (value: string): string[] => value.split(',').sort()

export function exploreViewFromSearch(search: URLSearchParams, limits: ExploreLimits): ExploreAddress {
  const versions = search.getAll('v')
  const one = versions.length === 0 || (versions.length === 1 && versions[0] === '1')
  if (!one) {
    // One whole number above 1 names a later version; any other v, a repeat included, is unreadable (Q26).
    const version = versions.length === 1 && WHOLE.test(versions[0]) ? 'other' : 'unreadable'
    return { version, view: { ...EXPLORE_DEFAULT_VIEW, seeds: [] }, selected: null, invalid: [], unknown: [] }
  }
  const view: ExploreView = { ...EXPLORE_DEFAULT_VIEW, seeds: [] }
  let selected: ExploreSelectedRef | null = null
  const bad = new Set<ExploreParamName>()
  for (const name of EXPLORE_PARAM_NAMES) {
    if (name === 'v') continue
    const values = search.getAll(name)
    if (values.length === 0) continue
    if (values.length > 1 || !exploreValueValid(name, values[0], limits)) {
      bad.add(name)
      continue
    }
    const value = values[0]
    switch (name) {
      case 'seeds': view.seeds = value.split(','); break
      case 'follow': view.follow = false; break
      case 'outcome': view.allOutcomes = true; break
      case 'layers': view.layers = sorted(value); break
      case 'direction': view.direction = value as GraphDirection; break
      case 'effect': view.effect = sorted(value) as GraphEffectKey[]; break
      case 'evidence_basis': view.evidence_basis = sorted(value); break
      case 'method': view.method = sorted(value); break
      case 'score_types': view.score_types = sorted(value); break
      case 'taxon': view.taxon = Number(value); break
      case 'releases': view.releases = sorted(value); break
      case 'browser': view.browser = value; break
      case 'selected': selected = value; break
      default: break
    }
  }
  if (search.getAll('releases').length > 0 && search.getAll('browser').length > 0) {
    bad.add('releases')
    bad.add('browser')
    view.releases = null
    view.browser = null
  }
  const unknown: string[] = []
  search.forEach((_value, name) => {
    if (!isKnown(name) && unknown.indexOf(name) === -1) unknown.push(name)
  })
  const invalid = EXPLORE_PARAM_NAMES.filter((name) => bad.has(name))
  return { version: 1, view, selected, invalid, unknown }
}

export function searchOfExploreView(view: ExploreView, selected: ExploreSelectedRef | null = null, keep?: URLSearchParams): URLSearchParams {
  const search = new URLSearchParams()
  search.set('v', '1')
  const list = (name: ExploreParamName, values: readonly string[] | null) => {
    if (values !== null && values.length > 0) search.set(name, values.join(','))
  }
  list('seeds', view.seeds)
  if (!view.follow) search.set('follow', 'off')
  if (view.allOutcomes) search.set('outcome', 'all')
  list('layers', view.layers)
  if (view.direction !== null) search.set('direction', view.direction)
  list('effect', view.effect)
  list('evidence_basis', view.evidence_basis)
  list('method', view.method)
  list('score_types', view.score_types)
  if (view.taxon !== null) search.set('taxon', String(view.taxon))
  if (view.browser === null) list('releases', view.releases)
  if (view.browser !== null && view.releases === null) search.set('browser', view.browser)
  if (selected !== null) search.set('selected', selected)
  if (keep !== undefined) {
    keep.forEach((value, name) => {
      if (!isKnown(name)) search.append(name, value)
    })
  }
  return search
}

export function exploreSearchString(search: URLSearchParams): string {
  const text = search.toString()
  return text === '' ? '' : `?${text}`
}

export function withoutExploreNames(search: URLSearchParams, names: readonly ExploreParamName[]): URLSearchParams {
  const next = new URLSearchParams(search)
  for (const name of names) next.delete(name)
  return next
}

export function sameExploreView(a: ExploreView, b: ExploreView): boolean {
  return searchOfExploreView(a).toString() === searchOfExploreView(b).toString()
}

export function exploreSeedsView(seeds: readonly GraphEntityRef[], selection: GraphSelection = {}): ExploreView {
  const browser = typeof selection.browser === 'string' && selection.browser !== '' ? selection.browser : null
  const given = selection.releases
  const releases = browser === null && Array.isArray(given) && given.length > 0 ? Array.from(new Set(given)).sort() : null
  return { ...EXPLORE_DEFAULT_VIEW, seeds: [...seeds], releases, browser }
}

export function exploreHref(view: ExploreView = EXPLORE_DEFAULT_VIEW): string {
  return `${EXPLORE_PATH}?${searchOfExploreView(view).toString()}`
}

export function exploreNetworkParams(view: ExploreView): GraphNetworkParams | null {
  // With no seed the page sends no network request (Q25).
  if (view.seeds.length === 0) return null
  const params = {
    seeds: [...view.seeds],
    expand: view.follow ? ('reference' as const) : undefined,
    layers: view.layers === null ? undefined : [...view.layers],
    direction: view.direction ?? undefined,
    outcome: view.allOutcomes ? ('all' as const) : undefined,
    // The registry's open set: the API judges each value (7.3).
    evidence_basis: view.evidence_basis === null ? undefined : ([...view.evidence_basis] as GraphEvidenceBasis[]),
    effect: view.effect === null ? undefined : [...view.effect],
    method: view.method === null ? undefined : [...view.method],
    // Every finite score of each type: one `<slug>::` per type, sent repeated (Q26).
    score: view.score_types === null ? undefined : view.score_types.map((slug) => `${slug}::`),
    taxon: view.taxon ?? undefined,
  }
  if (view.browser !== null && view.releases === null) return { ...params, browser: view.browser }
  if (view.releases !== null && view.browser === null) return { ...params, releases: [...view.releases] }
  return params
}

export const EXPLORE_WIRE_NAMES: readonly (readonly [string, ExploreParamName])[] = [
  ['seeds', 'seeds'], ['layers', 'layers'], ['expand', 'follow'], ['direction', 'direction'], ['outcome', 'outcome'],
  ['evidence_basis', 'evidence_basis'], ['effect', 'effect'], ['method', 'method'], ['score', 'score_types'], ['taxon', 'taxon'],
  ['releases', 'releases'], ['browser', 'browser'],
]

export function exploreNamesOfProblems(parameters: readonly string[]): ExploreParamName[] {
  const named = new Set<ExploreParamName>()
  for (const parameter of parameters) {
    for (const [wire, name] of EXPLORE_WIRE_NAMES) if (wire === parameter) named.add(name)
  }
  return EXPLORE_PARAM_NAMES.filter((name) => named.has(name))
}

export function exploreSelected(ref: ExploreSelectedRef | null): TypedSelection | null {
  if (ref === null) return null
  if (ENTITY_REF.test(ref)) return { kind: 'node', id: ref }
  if (RELATIONSHIP_REF.test(ref)) return { kind: 'edge', id: ref }
  return null
}

export function selectedRef(selection: { kind: string; id: string } | null): ExploreSelectedRef | null {
  if (selection === null) return null
  if (selection.kind === 'node' && ENTITY_REF.test(selection.id)) return selection.id
  if (selection.kind === 'edge' && RELATIONSHIP_REF.test(selection.id)) return selection.id
  return null
}

export type ExploreControlGroup =
  | 'follow' | 'outcome' | 'layers' | 'direction' | 'effect' | 'evidence_basis' | 'method' | 'score_types' | 'taxon'
export type ExploreChange = 'seeds' | 'scope' | 'clear' | 'selected' | ExploreControlGroup
export const EXPLORE_CONTROL_GROUPS: readonly ExploreControlGroup[] = [
  'follow', 'outcome', 'layers', 'direction', 'effect', 'evidence_basis', 'method', 'score_types', 'taxon',
]
export const EXPLORE_CONTROL_KEY = 'exploreControl'
export interface ExploreWrite { state: Record<string, unknown>; replace: boolean }

export function exploreWrite(locationState: unknown, change: ExploreChange): ExploreWrite {
  const base: Record<string, unknown> = typeof locationState === 'object' && locationState !== null && !Array.isArray(locationState)
    ? { ...(locationState as Record<string, unknown>) }
    : {}
  if (change === 'selected') return { state: base, replace: true }
  if (change === 'seeds' || change === 'scope' || change === 'clear') {
    const rest: Record<string, unknown> = { ...base }
    delete rest[EXPLORE_CONTROL_KEY]
    return { state: rest, replace: false }
  }
  return { state: { ...base, [EXPLORE_CONTROL_KEY]: change }, replace: base[EXPLORE_CONTROL_KEY] === change }
}
