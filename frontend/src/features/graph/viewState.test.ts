import { hashKey } from '@tanstack/react-query'
import ts from 'typescript'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { apiClient } from '../../api/client'
import { graphNetworkOptions } from '../../api/graph/network'
import { networkWire, normalizeNetworkParams } from '../../api/graph/params'
import type { GraphNetworkParams } from '../../api/graph/params'
import { GRAPH_PARAMS_SERIALIZER } from '../../api/graph/request'
import { recorded } from '../../test/graphHooks'
import type { GraphStatus } from './types'
import {
  EXPLORE_CONTROL_GROUPS, EXPLORE_CONTROL_KEY, EXPLORE_DEFAULT_VIEW, EXPLORE_LIMIT_FALLBACKS, EXPLORE_PARAM_NAMES, EXPLORE_PATH,
  EXPLORE_VIEW_VERSION, EXPLORE_WIRE_NAMES, exploreHref, exploreLimits, exploreNamesOfProblems, exploreNetworkParams, exploreSearchString,
  exploreSeedsView, exploreSelected, exploreValueValid, exploreViewFromSearch, exploreWrite, sameExploreView, searchOfExploreView,
  selectedRef, withoutExploreNames,
} from './viewState'
import type { ExploreChange, ExploreParamName, ExploreSelectedRef, ExploreView } from './viewState'

// X1 spec 12.1 (XV): pure; 12.0 item 2's hooks.
beforeEach(() => {
  vi.spyOn(console, 'warn')
  vi.spyOn(console, 'error')
})

afterEach(() => {
  // Nothing renders here, so there is nothing to unmount before the calls are read.
  const calls = [...vi.mocked(console.warn).mock.calls, ...vi.mocked(console.error).mock.calls]
  vi.restoreAllMocks()
  expect(calls.map((args) => args.map(String).join(' ').slice(0, 300))).toEqual([])
})

const L = EXPLORE_LIMIT_FALLBACKS
/** F1's own helper (FE/api/graph/params.test.ts:54-56, 63-64), so a request reads as the client sends it. */
const networkUri = (params: GraphNetworkParams) => apiClient.getUri({
  url: '/graph/v1/network', params: networkWire(normalizeNetworkParams(params)), paramsSerializer: GRAPH_PARAMS_SERIALIZER,
})
/** 7.4's view, written out. */
const FULL: ExploreView = {
  ...EXPLORE_DEFAULT_VIEW, seeds: ['entity:7'], follow: false, allOutcomes: true, layers: ['physical_interaction', 'transcriptional_regulation'],
  direction: 'out', effect: ['activating'], evidence_basis: ['experimental'], method: ['MI:0018'], score_types: ['synthetic:confidence'],
  taxon: 32630, releases: ['release:1'],
}
const seeded = exploreSeedsView(['entity:7'])
/** XF-2's ten single-filter views. */
const SINGLE: ExploreView[] = [
  { ...seeded, releases: ['release:1'] },
  { ...seeded, browser: 'synthetic-regulation' },
  { ...seeded, layers: ['transcriptional_regulation'] },
  { ...seeded, direction: 'out' },
  { ...seeded, effect: ['activating'] },
  { ...seeded, allOutcomes: true },
  { ...seeded, evidence_basis: ['experimental'] },
  { ...seeded, method: ['MI:0018'] },
  { ...seeded, score_types: ['synthetic:confidence'] },
  { ...seeded, taxon: 9606 },
]
const refs = (prefix: string, count: number) => Array.from({ length: count }, (_, index) => `${prefix}:${index + 1}`)
const ELEVEN = refs('entity', 11).join(',')

type Fields = Partial<ExploreView>
// 7.3's example table, the row "seeds= and eleven distinct refs" as two, then the six of v (critique M40).
const READS: [string, ExploreViewFromSearchVersion, Fields, ExploreSelectedRef | null, ExploreParamName[], string[]][] = [
  ['', 1, {}, null, [], []],
  ['seeds=entity:7', 1, { seeds: ['entity:7'] }, null, [], []],
  ['v=1&seeds=entity%3A7%2Centity%3A13&follow=off&outcome=all&utm_source=news', 1, { seeds: ['entity:7', 'entity:13'], follow: false, allOutcomes: true }, null, [], ['utm_source']],
  ['v=2&seeds=entity:7', 'other', {}, null, [], []],
  ['v=1&v=1&seeds=entity:7', 'unreadable', {}, null, [], []],
  ['v=&seeds=entity:7', 'unreadable', {}, null, [], []],
  ['seeds=entity:7,entity:7', 1, {}, null, ['seeds'], []],
  ['seeds=entity:0', 1, {}, null, ['seeds'], []],
  ['seeds=', 1, {}, null, ['seeds'], []],
  [`seeds=${ELEVEN}`, 1, {}, null, ['seeds'], []],
  ['seeds=entity:7&follow=on', 1, { seeds: ['entity:7'] }, null, ['follow'], []],
  ['layers=transcriptional_regulation,family:physical', 1, { layers: ['family:physical', 'transcriptional_regulation'] }, null, [], []],
  ['direction=out&direction=in', 1, {}, null, ['direction'], []],
  ['effect=inhibiting,activating', 1, { effect: ['activating', 'inhibiting'] }, null, [], []],
  ['effect=mixed', 1, {}, null, ['effect'], []],
  ['method=MI:0407,MI:0018', 1, { method: ['MI:0018', 'MI:0407'] }, null, [], []],
  ['method=mi:0018', 1, {}, null, ['method'], []],
  ['score_types=synthetic:confidence', 1, { score_types: ['synthetic:confidence'] }, null, [], []],
  ['taxon=32630', 1, { taxon: 32630 }, null, [], []],
  ['taxon=0', 1, {}, null, ['taxon'], []],
  ['releases=release:2&browser=synthetic-regulation', 1, {}, null, ['releases', 'browser'], []],
  ['browser=Synthetic-Regulation', 1, {}, null, ['browser'], []],
  ['selected=relationship:3', 1, {}, 'relationship:3', [], []],
  ['selected=edge:3', 1, {}, null, ['selected'], []],
  ['expand=reference&seeds=entity:7', 1, { seeds: ['entity:7'] }, null, [], ['expand']],
  ['taxon=x&seeds=', 1, {}, null, ['seeds', 'taxon'], []],
  ['v=12&seeds=entity:7', 'other', {}, null, [], []],
  ['v=0&seeds=entity:7', 'unreadable', {}, null, [], []],
  ['v=01&seeds=entity:7', 'unreadable', {}, null, [], []],
  ['v=x&seeds=entity:7', 'unreadable', {}, null, [], []],
  ['v=%2B2&seeds=entity:7', 'unreadable', {}, null, [], []],
  ['v=2&v=2&seeds=entity:7', 'unreadable', {}, null, [], []],
]
type ExploreViewFromSearchVersion = 1 | 'other' | 'unreadable'

const VALUES: [Exclude<ExploreParamName, 'v'>, string[], string[]][] = [
  ['seeds', ['entity:7', 'entity:1234567890123456789', refs('entity', 10).join(',')],
    ['', 'entity:0', 'entity:07', 'relationship:3', 'entity:7,entity:7', 'entity:7,', ELEVEN, 'entity:12345678901234567890']],
  ['follow', ['off'], ['on', 'OFF', 'Off', '']],
  ['outcome', ['all'], ['ALL', 'supported', '']],
  ['layers', ['family:physical', 'transcriptional_regulation,family:physical', refs('t', 20).map((piece) => piece.replace(':', '')).join(','), 'a' + 'b'.repeat(49)],
    ['', 'Physical', 'family:', 'a,a', 'a,', refs('t', 21).map((piece) => piece.replace(':', '')).join(','), 'a' + 'b'.repeat(50)]],
  ['direction', ['out', 'in', 'both'], ['Out', 'up', '']],
  ['effect', ['activating', 'not_stated', 'inhibiting,activating', 'activating,inhibiting,unknown,not_stated'], ['mixed', 'activating,', 'activating,activating', '']],
  ['evidence_basis', ['experimental', 'experimental,statistical'], ['Experimental', 'experimental,experimental', '']],
  ['method', ['MI:0018', 'MI:0407,MI:0018'], ['mi:0018', 'MI:18', 'MI:00180', 'MI:0018,MI:0018', '']],
  ['score_types', ['synthetic:confidence', 'synthetic:' + 'a'.repeat(190)], ['synthetic', 'Synthetic:confidence', 'synthetic:' + 'a'.repeat(191), '']],
  ['taxon', ['32630', '9606'], ['0', '01', '-1', '1e3', ' 9606', '99999999999999999999', '']],
  ['releases', ['release:1,release:2', refs('release', 50).join(',')], ['', 'release:0', 'release:1,release:1', refs('release', 51).join(',')]],
  ['browser', ['synthetic-regulation', 'a'.repeat(50)], ['Synthetic-Regulation', 'a--b', '-a', 'a-', 'a'.repeat(51), '']],
  ['selected', ['entity:10', 'relationship:3'], ['edge:3', 'release:1', 'entity:0', '']],
]

// 7.5's request table.
const REQUESTS: [string, ExploreView, string | null][] = [
  ["exploreSeedsView(['entity:7'])", seeded, '/api/graph/v1/network?seeds=entity:7&expand=reference'],
  ['the same, follow false', { ...seeded, follow: false }, '/api/graph/v1/network?seeds=entity:7'],
  ['the same, allOutcomes true', { ...seeded, allOutcomes: true }, '/api/graph/v1/network?seeds=entity:7&expand=reference&outcome=all'],
  ["the same, releases ['release:1']", { ...seeded, releases: ['release:1'] }, '/api/graph/v1/network?seeds=entity:7&expand=reference&releases=release:1'],
  ["the same, layers ['transcriptional_regulation']", { ...seeded, layers: ['transcriptional_regulation'] },
    '/api/graph/v1/network?seeds=entity:7&layers=transcriptional_regulation&expand=reference'],
  ["exploreSeedsView(['entity:9']), score_types ['synthetic:confidence']", { ...exploreSeedsView(['entity:9']), score_types: ['synthetic:confidence'] },
    '/api/graph/v1/network?seeds=entity:9&expand=reference&score=synthetic:confidence::'],
  ["exploreSeedsView(['entity:7', 'entity:13'])", exploreSeedsView(['entity:7', 'entity:13']), '/api/graph/v1/network?seeds=entity:13,entity:7&expand=reference'],
  ['EXPLORE_DEFAULT_VIEW', EXPLORE_DEFAULT_VIEW, null],
]

const definedKeys = (params: object | null) => Object.entries(params ?? {}).filter(([, value]) => value !== undefined).map(([key]) => key)

describe('the view state', () => {
  it('XV-1: the constants, and the caps read from status with their fallbacks', () => {
    expect(EXPLORE_PATH).toBe('/explore')
    expect(EXPLORE_VIEW_VERSION).toBe(1)
    expect(EXPLORE_PARAM_NAMES).toEqual([
      'v', 'seeds', 'follow', 'outcome', 'layers', 'direction', 'effect', 'evidence_basis', 'method', 'score_types', 'taxon', 'releases', 'browser', 'selected',
    ])
    expect(EXPLORE_DEFAULT_VIEW).toEqual({
      v: 1, seeds: [], follow: true, allOutcomes: false, layers: null, direction: null, effect: null, evidence_basis: null,
      method: null, score_types: null, taxon: null, releases: null, browser: null,
    })
    expect(EXPLORE_LIMIT_FALLBACKS).toEqual({ maxSeeds: 10, maxReleases: 50, maxLayers: 20 })
    expect(EXPLORE_CONTROL_GROUPS).toEqual(['follow', 'outcome', 'layers', 'direction', 'effect', 'evidence_basis', 'method', 'score_types', 'taxon'])
    expect(EXPLORE_CONTROL_KEY).toBe('exploreControl')
    const on = recorded<GraphStatus>('status-on').body
    expect(exploreLimits(on)).toEqual({ maxSeeds: 10, maxReleases: 50, maxLayers: 20 })
    const small = { ...on, limits: { ...on.limits, max_seeds: 3, max_releases: 2, max_layers: 1 } } as GraphStatus
    expect(exploreLimits(small)).toEqual({ maxSeeds: 3, maxReleases: 2, maxLayers: 1 })
    const noLimits = { ...on } as Partial<GraphStatus>
    delete noLimits.limits
    for (const status of [undefined, null, noLimits as GraphStatus]) expect(exploreLimits(status)).toEqual(EXPLORE_LIMIT_FALLBACKS)
    const fields: [string, keyof typeof EXPLORE_LIMIT_FALLBACKS][] = [['max_seeds', 'maxSeeds'], ['max_releases', 'maxReleases'], ['max_layers', 'maxLayers']]
    for (const [field, key] of fields) {
      for (const value of [0, 1.5, '10']) {
        const status = { ...small, limits: { ...small.limits, [field]: value } } as unknown as GraphStatus
        expect(exploreLimits(status), `${field} ${String(value)}`).toEqual({ ...exploreLimits(small), [key]: EXPLORE_LIMIT_FALLBACKS[key] })
      }
    }
  })

  it.each(READS)('XV-2: each example address reads as 7.3 gives it, and reading never rewrites the address (%s)', (text, version, fields, selected, invalid, unknown) => {
    const search = new URLSearchParams(text)
    const before = search.toString()
    const result = exploreViewFromSearch(search, L)
    expect(result).toEqual({ version, view: { ...EXPLORE_DEFAULT_VIEW, ...fields }, selected, invalid, unknown })
    if (version !== 1) expect(result.view).toEqual(EXPLORE_DEFAULT_VIEW)
    expect(search.toString()).toBe(before)
    expect(result.view.seeds).not.toBe(EXPLORE_DEFAULT_VIEW.seeds)
  })

  it.each(VALUES)("XV-3: each name's grammar takes its valid values and refuses its malformed ones (%s)", (name, valid, invalid) => {
    for (const value of valid) expect(exploreValueValid(name, value, L), value.slice(0, 80)).toBe(true)
    for (const value of invalid) expect(exploreValueValid(name, value, L), value.slice(0, 80)).toBe(false)
    if (name === 'seeds') {
      expect(exploreValueValid('seeds', 'entity:7', { ...L, maxSeeds: 1 })).toBe(true)
      expect(exploreValueValid('seeds', 'entity:7,entity:13', { ...L, maxSeeds: 1 })).toBe(false)
    }
    if (name === 'taxon') expect(exploreViewFromSearch(new URLSearchParams('taxon=+9606'), L).invalid).toEqual(['taxon'])
  })

  it('XV-4: each example view writes as 7.4 gives it, unknown names kept, and every view reads back whole', () => {
    expect(searchOfExploreView(EXPLORE_DEFAULT_VIEW).toString()).toBe('v=1')
    expect(searchOfExploreView({ ...EXPLORE_DEFAULT_VIEW, seeds: ['entity:7', 'entity:13'] }).toString()).toBe('v=1&seeds=entity%3A7%2Centity%3A13')
    expect(searchOfExploreView(FULL, 'relationship:3', new URLSearchParams('utm_source=news&seeds=entity:9&v=7')).toString()).toBe(
      'v=1&seeds=entity%3A7&follow=off&outcome=all&layers=physical_interaction%2Ctranscriptional_regulation&direction=out&effect=activating'
      + '&evidence_basis=experimental&method=MI%3A0018&score_types=synthetic%3Aconfidence&taxon=32630&releases=release%3A1&selected=relationship%3A3'
      + '&utm_source=news',
    )
    expect(searchOfExploreView({ ...EXPLORE_DEFAULT_VIEW, seeds: ['entity:7'], browser: 'synthetic-regulation' }).toString())
      .toBe('v=1&seeds=entity%3A7&browser=synthetic-regulation')
    expect(searchOfExploreView({ ...EXPLORE_DEFAULT_VIEW, seeds: ['entity:7'], releases: ['release:1'], browser: 'synthetic-regulation' }).toString())
      .toBe('v=1&seeds=entity%3A7')
    expect(searchOfExploreView(EXPLORE_DEFAULT_VIEW, null, new URLSearchParams('utm=a&seeds=entity:9&utm=b&v=7')).toString()).toBe('v=1&utm=a&utm=b')
    const trips: [ExploreView, ExploreSelectedRef | null][] = [[FULL, 'relationship:3'], [EXPLORE_DEFAULT_VIEW, null], ...SINGLE.map((view): [ExploreView, null] => [view, null])]
    for (const [view, selected] of trips) {
      expect(exploreViewFromSearch(searchOfExploreView(view, selected), L)).toEqual({ version: 1, view, selected, invalid: [], unknown: [] })
    }
  })

  it('XV-5: the search string, removal, sameness, seeds view and href helpers', () => {
    expect(exploreSearchString(new URLSearchParams())).toBe('')
    expect(exploreSearchString(new URLSearchParams('v=1&seeds=entity%3A7'))).toBe('?v=1&seeds=entity%3A7')
    const params = new URLSearchParams('v=1&seeds=entity:7&taxon=0&utm_source=news')
    const before = params.toString()
    expect(withoutExploreNames(params, ['taxon']).toString()).toBe('v=1&seeds=entity%3A7&utm_source=news')
    expect(params.toString()).toBe(before)
    expect(sameExploreView(FULL, { ...FULL })).toBe(true)
    expect(sameExploreView(FULL, { ...FULL, follow: true })).toBe(false)
    const noReleases = exploreSeedsView(['entity:7'], { releases: [] })
    expect([noReleases.releases, noReleases.browser]).toEqual([null, null])
    const noBrowser = exploreSeedsView(['entity:7'], { browser: '' })
    expect([noBrowser.releases, noBrowser.browser]).toEqual([null, null])
    expect(exploreHref()).toBe('/explore?v=1')
    expect(exploreHref(exploreSeedsView(['entity:7', 'entity:13']))).toBe('/explore?v=1&seeds=entity%3A7%2Centity%3A13')
    expect(exploreHref(exploreSeedsView(['entity:7'], { browser: 'synthetic-regulation' }))).toBe('/explore?v=1&seeds=entity%3A7&browser=synthetic-regulation')
    expect(exploreHref(exploreSeedsView(['entity:10'], { releases: ['release:2', 'release:1', 'release:2'] })))
      .toBe('/explore?v=1&seeds=entity%3A10&releases=release%3A1%2Crelease%3A2')
  })

  it('XV-6: the history rule pushes and replaces as 7.4 gives it and keeps the way back', () => {
    expect(exploreWrite(null, 'follow')).toEqual({ state: { exploreControl: 'follow' }, replace: false })
    expect(exploreWrite({ exploreControl: 'follow' }, 'follow')).toEqual({ state: { exploreControl: 'follow' }, replace: true })
    expect(exploreWrite({ exploreControl: 'follow' }, 'effect')).toEqual({ state: { exploreControl: 'effect' }, replace: false })
    expect(exploreWrite({ exploreControl: 'layers', other: 1 }, 'seeds')).toEqual({ state: { other: 1 }, replace: false })
    expect(exploreWrite({ exploreControl: 'layers' }, 'selected')).toEqual({ state: { exploreControl: 'layers' }, replace: true })
    expect(exploreWrite('text', 'scope')).toEqual({ state: {}, replace: false })
    const back = { pathname: '/browsers/synthetic-regulation', search: '', title: 'x', state: null }
    for (const change of ['seeds', 'scope', 'clear', 'selected', 'taxon'] as ExploreChange[]) {
      expect(exploreWrite({ exploreBack: back, exploreControl: 'layers' }, change).state.exploreBack, change).toBe(back)
    }
  })

  it("XV-7: the selection maps to the workspace's selection and back, for refs of the two grammars only", () => {
    expect(exploreSelected(null)).toBeNull()
    expect(exploreSelected('entity:10')).toEqual({ kind: 'node', id: 'entity:10' })
    expect(exploreSelected('relationship:1')).toEqual({ kind: 'edge', id: 'relationship:1' })
    expect(exploreSelected('edge:1' as never)).toBeNull()
    expect(selectedRef(null)).toBeNull()
    expect(selectedRef({ kind: 'node', id: 'entity:10' })).toBe('entity:10')
    expect(selectedRef({ kind: 'edge', id: 'relationship:1' })).toBe('relationship:1')
    expect(selectedRef({ kind: 'node', id: 'relationship:1' })).toBeNull()
    expect(selectedRef({ kind: 'edge', id: 'entity:10' })).toBeNull()
    expect(selectedRef({ kind: 'edge', id: 'x' })).toBeNull()
  })

  it.each(REQUESTS)("XV-8: each view sends 7.5's request, follow off sends the plain request, and no unknown name is sent (%s)", (_name, view, request) => {
    if (request === null) expect(exploreNetworkParams(view)).toBeNull()
    else expect(networkUri(exploreNetworkParams(view) as GraphNetworkParams)).toBe(request)
    const plain = exploreNetworkParams({ ...seeded, follow: false }) as GraphNetworkParams
    expect(plain).toEqual({ seeds: ['entity:7'] })
    expect(hashKey(graphNetworkOptions('visitor', plain).queryKey)).toBe(hashKey(graphNetworkOptions('visitor', { seeds: ['entity:7'] }).queryKey))
    const address = exploreViewFromSearch(
      new URLSearchParams('seeds=entity:7&expand=reference&level=association&max_edges=5&include_partner_edges=true&context.tissue=liver'), L,
    )
    expect(definedKeys(exploreNetworkParams(address.view))).toEqual(['seeds', 'expand'])
  })

  it("XV-9: the wire names map a 400's parameters back to address names", () => {
    expect(EXPLORE_WIRE_NAMES).toEqual([
      ['seeds', 'seeds'], ['layers', 'layers'], ['expand', 'follow'], ['direction', 'direction'], ['outcome', 'outcome'],
      ['evidence_basis', 'evidence_basis'], ['effect', 'effect'], ['method', 'method'], ['score', 'score_types'], ['taxon', 'taxon'],
      ['releases', 'releases'], ['browser', 'browser'],
    ])
    expect(exploreNamesOfProblems(['score', 'expand'])).toEqual(['follow', 'score_types'])
    expect(exploreNamesOfProblems(['layers', 'layers'])).toEqual(['layers'])
    expect(exploreNamesOfProblems(['level'])).toEqual([])
    expect(exploreNamesOfProblems([])).toEqual([])
    expect(exploreNamesOfProblems(['browser', 'releases', 'seeds'])).toEqual(['seeds', 'releases', 'browser'])
  })

  it('XV-10: viewState.ts imports types only', () => {
    const name = `${ts.sys.getCurrentDirectory()}/src/features/graph/viewState.ts`
    const text = ts.sys.readFile(name) ?? ''
    const source = ts.createSourceFile(name, text, ts.ScriptTarget.ES2020, true, ts.ScriptKind.TS)
    const imports = source.statements.filter(ts.isImportDeclaration)
    expect(imports.map((node) => (node.moduleSpecifier as ts.StringLiteral).text)).toEqual(['../../api/graph/params', './network/graph', './types'])
    for (const node of imports) expect(node.importClause?.isTypeOnly).toBe(true)
    expect(source.statements.filter(ts.isExportDeclaration).filter((node) => node.moduleSpecifier !== undefined)).toEqual([])
  })

  it('XV-11: an address at every count cap X1 keeps stays within nginx\'s line and reads back whole', () => {
    const nineteen = (index: number) => `1${String(index).padStart(18, '0')}`
    const view: ExploreView = {
      ...EXPLORE_DEFAULT_VIEW,
      seeds: Array.from({ length: 10 }, (_, index) => `entity:${nineteen(index + 1)}`),
      layers: Array.from({ length: 20 }, (_, index) => `l${String(index + 1).padStart(2, '0')}${'x'.repeat(47)}`),
      direction: 'out',
      effect: ['activating', 'inhibiting', 'not_stated', 'unknown'],
      evidence_basis: ['computational_inference', 'computational_prediction', 'experimental', 'statistical', 'text_mining', 'unknown'],
      method: Array.from({ length: 20 }, (_, index) => `MI:${String(index + 1).padStart(4, '0')}`),
      score_types: ['synthetic:confidence', 'synthetic:rank'],
      taxon: 32630,
      releases: Array.from({ length: 50 }, (_, index) => `release:${nineteen(index + 1)}`),
    }
    for (const list of [view.layers, view.releases]) for (const piece of list ?? []) expect(piece.length).toBe(list === view.layers ? 50 : 27)
    const search = searchOfExploreView(view, `relationship:${nineteen(1)}`).toString()
    expect(`GET ${EXPLORE_PATH}?${search} HTTP/1.1`.length).toBeLessThan(8190)
    expect(exploreViewFromSearch(new URLSearchParams(search), { maxSeeds: 10, maxReleases: 50, maxLayers: 20 }).invalid).toEqual([])
  })
})
