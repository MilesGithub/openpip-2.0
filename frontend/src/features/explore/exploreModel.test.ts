import ts from 'typescript'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { recorded } from '../../test/graphHooks'
import * as seedWords from '../browsers/seedWords'
import type { GraphStatus } from '../graph/types'
import { EXPLORE_DEFAULT_VIEW, exploreSeedsView } from '../graph/viewState'
import type { ExploreParamName, ExploreView } from '../graph/viewState'
import { isolateText } from '../graph/words/isolateText'
import { EXPLORE_BACK_KEY, expandedSeedLimit, exploreBackOf, exploreBackState, viewSelection, withoutViewNames } from './exploreModel'
import * as exploreWords from './exploreWords'

// X1 spec 12.6 (XM): pure; 12.0 item 2's hooks.
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

// Words copied from the spec's section 8.10, never from the module under test.
const SEARCH_HEADING = 'Find entities'
const SEARCH_UNAVAILABLE = 'The entity search is not available on this portal; the entities chosen for this view are still drawn.'
const SEARCH_UNAVAILABLE_EMPTY = 'The entity search is not available on this portal, and this view names no entity to draw.'
const NO_SEED_PROMPT = 'Search for one or more entities to draw their network.'
const NETWORK_HEADING = 'Network'
const CHOSEN_LABEL = 'Chosen entities'
const REMOVE_ALL = 'Remove all chosen entities'
const NEWER_VERSION = 'This view needs a newer version of openPIP.'
const UNREADABLE_VERSION = 'The explorer cannot read the version this address names.'
const NEWER_VERSION_LINK = 'Open the explorer with no entities chosen'
const EXPLORE_NAME_NOUNS = {
  v: 'version', seeds: 'chosen entities', follow: 'gene-to-protein setting', outcome: 'tested-absent setting', layers: 'layers',
  direction: 'direction', effect: 'effects', evidence_basis: 'evidence bases', method: 'detection methods', score_types: 'score types',
  taxon: 'partner organism', releases: 'releases', browser: 'browser', selected: 'selection',
}
const VIEW_PROBLEM = 'The network cannot use this view.'
const FOLLOW_COST = 'Following gene-to-protein links adds the entities they reach to the lookups; turn it off to ask for a smaller network.'
const NETWORK_AGAIN = 'Press Try again to ask for this network again.'

/** 8.3's example. */
const BACK = {
  pathname: '/browsers/synthetic-regulation', search: '?seeds=entity%3A7', title: 'Synthetic regulation example',
  state: { gallerySearch: '?tag=gene_regulation' },
}
const BANNED = /does not exist|not found|undefined|HTTP|No page uses|\b[Nn]one\b/

describe("the explorer's model", () => {
  it('XM-1: the way back holds an instance path, a search and a title, and refuses anything else', () => {
    expect(EXPLORE_BACK_KEY).toBe('exploreBack')
    expect(exploreBackState(BACK)).toEqual({ exploreBack: BACK })
    const back = exploreBackOf(exploreBackState(BACK))
    expect(back).toEqual(BACK)
    expect(back).not.toBe(BACK)
    expect(back?.state).toBe(BACK.state)
    const long = { ...BACK, pathname: `/browsers/${'a'.repeat(50)}` }
    expect(long.pathname).toHaveLength(60)
    expect(exploreBackOf({ exploreBack: long })).toEqual(long)
    const noTitle: Partial<typeof BACK> = { ...BACK }
    delete noTitle.title
    const refused: unknown[] = [
      'text', null, [], {}, { exploreBack: null }, { exploreBack: [] }, { exploreBack: 'x' },
      ...['/explore', '/browsers/A', '/browsers/a/b', '/browsers/', `/browsers/${'a'.repeat(51)}`, 5].map((pathname) => ({ exploreBack: { ...BACK, pathname } })),
      ...['seeds=x', 5].map((search) => ({ exploreBack: { ...BACK, search } })),
      ...['', 5].map((title) => ({ exploreBack: { ...BACK, title } })),
      { exploreBack: noTitle },
    ]
    for (const state of refused) expect(exploreBackOf(state), JSON.stringify(state)).toBeNull()
  })

  it("XM-2: the closure's cap comes from status or not at all", () => {
    const on = recorded<GraphStatus>('status-on').body
    expect(expandedSeedLimit(on)).toBe(26)
    expect(expandedSeedLimit({ ...on, limits: { ...on.limits, max_expanded_seeds: 1 } } as GraphStatus)).toBe(1)
    const noLimits = { ...on } as Partial<GraphStatus>
    delete noLimits.limits
    for (const status of [undefined, null, noLimits as GraphStatus]) expect(expandedSeedLimit(status)).toBeNull()
    for (const value of [0, 1.5, '26']) {
      expect(expandedSeedLimit({ ...on, limits: { ...on.limits, max_expanded_seeds: value } } as unknown as GraphStatus), String(value)).toBeNull()
    }
  })

  it("XM-3: the search's selection is the view's browser, else its releases", () => {
    expect(viewSelection(exploreSeedsView(['entity:7'], { browser: 'synthetic-regulation' }))).toEqual({ browser: 'synthetic-regulation' })
    const view = exploreSeedsView(['entity:7'], { releases: ['release:1'] })
    const selection = viewSelection(view)
    expect(selection).toEqual({ releases: ['release:1'] })
    expect(selection.releases).not.toBe(view.releases)
    expect(viewSelection(EXPLORE_DEFAULT_VIEW)).toEqual({})
  })

  it('XM-4: a name a 400 refused goes back to its default, and follow to off', () => {
    expect(withoutViewNames({ ...EXPLORE_DEFAULT_VIEW, seeds: ['entity:7'], layers: ['syn_unknown'] }, ['layers']))
      .toEqual({ ...EXPLORE_DEFAULT_VIEW, seeds: ['entity:7'] })
    expect(withoutViewNames({ ...EXPLORE_DEFAULT_VIEW, seeds: ['entity:7'] }, ['follow']))
      .toEqual({ ...EXPLORE_DEFAULT_VIEW, seeds: ['entity:7'], follow: false })
    const every: ExploreView = {
      ...EXPLORE_DEFAULT_VIEW, seeds: ['entity:7'], follow: true, allOutcomes: true, layers: ['transcriptional_regulation'], direction: 'out',
      effect: ['activating'], evidence_basis: ['experimental'], method: ['MI:0018'], score_types: ['synthetic:confidence'], taxon: 9606,
      releases: ['release:1'], browser: 'synthetic-regulation',
    }
    const copy = JSON.parse(JSON.stringify(every)) as ExploreView
    const back: [ExploreParamName, keyof ExploreView, unknown][] = [
      ['seeds', 'seeds', []], ['follow', 'follow', false], ['outcome', 'allOutcomes', false], ['layers', 'layers', null], ['direction', 'direction', null],
      ['effect', 'effect', null], ['evidence_basis', 'evidence_basis', null], ['method', 'method', null], ['score_types', 'score_types', null],
      ['taxon', 'taxon', null], ['releases', 'releases', null], ['browser', 'browser', null],
    ]
    for (const [name, field, value] of back) {
      const result = withoutViewNames(every, [name])
      expect(result, name).toEqual({ ...every, [field]: value })
      expect(result).not.toBe(every)
    }
    // Each but follow is EXPLORE_DEFAULT_VIEW's value.
    for (const [name, field, value] of back) if (name !== 'follow') expect(value, name).toEqual(EXPLORE_DEFAULT_VIEW[field])
    for (const name of ['v', 'selected'] as ExploreParamName[]) {
      const result = withoutViewNames(every, [name])
      expect(result).toEqual(every)
      expect(result).not.toBe(every)
    }
    expect(every).toEqual(copy)
  })

  it('XM-5: the words of exploreWords.ts', () => {
    expect([
      exploreWords.SEARCH_HEADING, exploreWords.SEARCH_UNAVAILABLE, exploreWords.SEARCH_UNAVAILABLE_EMPTY, exploreWords.NO_SEED_PROMPT,
      exploreWords.NETWORK_HEADING, exploreWords.CHOSEN_LABEL, exploreWords.REMOVE_ALL, exploreWords.NEWER_VERSION, exploreWords.UNREADABLE_VERSION,
      exploreWords.NEWER_VERSION_LINK, exploreWords.VIEW_PROBLEM, exploreWords.FOLLOW_COST, exploreWords.NETWORK_AGAIN,
    ]).toEqual([
      SEARCH_HEADING, SEARCH_UNAVAILABLE, SEARCH_UNAVAILABLE_EMPTY, NO_SEED_PROMPT, NETWORK_HEADING, CHOSEN_LABEL, REMOVE_ALL, NEWER_VERSION,
      UNREADABLE_VERSION, NEWER_VERSION_LINK, VIEW_PROBLEM, FOLLOW_COST, NETWORK_AGAIN,
    ])
    expect(exploreWords.EXPLORE_NAME_NOUNS).toEqual(EXPLORE_NAME_NOUNS)
    const outputs: [string, string][] = [
      [exploreWords.addressProblemText([]), 'The explorer cannot use this address.'],
      [exploreWords.addressProblemText(['seeds']), 'The explorer cannot use the chosen entities in this address.'],
      [exploreWords.addressProblemText(['follow', 'taxon']), 'The explorer cannot use the gene-to-protein setting and partner organism in this address.'],
      [exploreWords.addressProblemText(['layers', 'score_types', 'taxon']), 'The explorer cannot use the layers, score types and partner organism in this address.'],
      [exploreWords.addressRemovalText([]), 'Open the explorer with no entities chosen'],
      [exploreWords.addressRemovalText(['releases', 'browser']), 'Show the explorer without the releases and browser'],
      [exploreWords.viewProblemText([]), 'The network cannot use this view.'],
      [exploreWords.viewProblemText(['layers']), 'The network cannot use the layers of this view.'],
      [exploreWords.viewProblemText(['layers', 'score_types', 'taxon']), 'The network cannot use the layers, score types and partner organism of this view.'],
      [exploreWords.viewRemovalText(['layers']), 'Remove the layers from this view'],
      [exploreWords.viewRemovalText(['follow']), 'Remove the gene-to-protein setting from this view'],
      [exploreWords.viewRemovalText([]), 'Remove the values from this view'],
      [exploreWords.backText('Synthetic regulation example'), `Back to ${isolateText('Synthetic regulation example')}`],
      [exploreWords.backText('a'.repeat(1001)), `Back to ${isolateText(`${'a'.repeat(1000)}… (1 more character not shown)`)}`],
    ]
    for (const [got, want] of outputs) expect(got).toBe(want)
    expect(exploreWords.NETWORK_HEADING).toBe(seedWords.NETWORK_HEADING)
    expect(exploreWords.CHOSEN_LABEL).toBe(seedWords.CHOSEN_LABEL)
    expect(exploreWords.REMOVE_ALL).toBe(seedWords.REMOVE_ALL)
    expect(exploreWords.removeText('SYN-TF1 (Gene)')).toBe(seedWords.removeText('SYN-TF1 (Gene)'))
    const strings = [
      ...(Object.values(exploreWords) as unknown[]).filter((value): value is string => typeof value === 'string'),
      ...Object.values(exploreWords.EXPLORE_NAME_NOUNS),
      ...outputs.map(([got]) => got),
      exploreWords.removeText('SYN-TF1 (Gene)'),
    ]
    expect(strings.filter((value) => value.includes('not enabled') || BANNED.test(value))).toEqual([])
  })

  it('XM-6: exploreModel.ts brings viewState.ts and nothing more', () => {
    const name = `${ts.sys.getCurrentDirectory()}/src/features/explore/exploreModel.ts`
    const source = ts.createSourceFile(name, ts.sys.readFile(name) ?? '', ts.ScriptTarget.ES2020, true, ts.ScriptKind.TS)
    const imports = source.statements.filter(ts.isImportDeclaration)
    expect(imports.map((node) => (node.moduleSpecifier as ts.StringLiteral).text))
      .toEqual(['../../api/graph/params', '../graph/types', '../graph/viewState', '../graph/viewState'])
    expect(imports.map((node) => node.importClause?.isTypeOnly ?? false)).toEqual([true, true, false, true])
    const third = imports[2].importClause?.namedBindings as ts.NamedImports
    expect(third.elements.map((element) => element.name.text)).toEqual(['EXPLORE_DEFAULT_VIEW'])
  })
})
