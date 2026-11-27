import { act, cleanup, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import type { JsonBodyType } from 'msw'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import type { MockInstance } from 'vitest'
import { closureDropNetwork, methodFilterNetwork } from '../../mocks/fixtures/typedGraph/extras'
import { typedGraphOffHandlers, typedGraphVisitorHandlers } from '../../mocks/handlers/typedGraph'
import { server } from '../../mocks/server'
import { useAuthStore } from '../../store/authStore'
import { GRAPH_WAIT, recordRequests, recorded } from '../../test/graphHooks'
import type { RequestLog } from '../../test/graphHooks'
import { recordAnswers, renderGraphRoute } from '../../test/graphRoutes'
import type { GraphRouteRender } from '../../test/graphRoutes'
import type { GraphEvidenceResponse, GraphNetworkQuery, GraphNetworkResponse, GraphStatus } from '../graph/types'
import { isolateText } from '../graph/words/isolateText'

// X1 spec 12.7 (XP): /explore through the route helper, the canvas mocked.
vi.mock('../graph/network/TypedNetwork', () => ({ TypedNetwork: () => null }))

let requests: RequestLog
let answers: { answers: string[]; stop: () => void }
let scrolls: MockInstance

// The first import of a page transforms its modules, which can take longer than a row's waits on a loaded host.
beforeAll(async () => {
  await import('./ExplorePage')
  await import('../browsers/InstancePage')
}, 60_000)

beforeEach(() => {
  useAuthStore.getState().logout()
  requests = recordRequests()
  answers = recordAnswers()
  vi.spyOn(console, 'warn')
  vi.spyOn(console, 'error')
  // jsdom prints "Not implemented" for a call no spy replaces, which no console spy sees.
  scrolls = vi.spyOn(window, 'scrollTo').mockImplementation(() => {})
})

afterEach(() => {
  // F3's panels print a status, so an answer from MSW's miss would show here before the page goes.
  expect(document.body.textContent ?? '').not.toContain('HTTP 501')
  // Unmounted first, so that what the unmount reports is checked too.
  cleanup()
  requests.stop()
  answers.stop()
  const calls = [...vi.mocked(console.warn).mock.calls, ...vi.mocked(console.error).mock.calls]
  vi.restoreAllMocks()
  expect(requests.unhandled).toEqual([])
  expect(answers.answers.filter((answer) => answer.startsWith('501 '))).toEqual([])
  expect(calls.map((args) => args.map(String).join(' ').slice(0, 300))).toEqual([])
})

// Words copied from X1's spec, sections 7.9, 7.12, 8.10, 9.3 and 11.4, with F2's, F3's and F4's words of 12.0, never from the modules under test.
const i = isolateText
const EXPLORE_TITLE = 'Explore the unified network'
const EXPLORE_NOT_ENABLED = 'The unified network is not enabled on this portal.'
const EXPLORE_ACTION = 'Explore in the unified network'
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
const removeText = (name: string) => `Remove ${i(name)}`
const backText = (title: string) => `Back to ${i(title)}`
const TOGGLES_NAME = 'Network options'
const FOLLOW_LABEL = 'Follow gene-to-protein links'
const FOLLOW_NOTE = "Adds what reference links join to each chosen entity: a gene's protein entries and their isoforms, an entry's isoforms and genes, and an isoform's entry and that entry's genes."
const OUTCOME_LABEL = 'Show tested-absent results'
const OUTCOME_NOTE = 'Also lists inconclusive and not-significant results: every result that does not support a relationship. Off, the network lists supporting results only.'
const LAYERS_LEGEND = 'Layers'
const LAYERS_MINIMUM = 'At least one layer stays on.'
const FILTERS_NAME = 'Filters'
const DIRECTION_LABEL = 'Direction from each seed'
const METHOD_LABEL = 'Detection method'
const METHOD_APPLY = 'Apply the detection methods'
const TAXON_LABEL = 'Organism of the partners'
const RELEASES_LEGEND = 'Releases'
const RELEASES_PINNED = 'Only the checked releases are read.'
const CLEAR_FILTERS = 'Clear filters'
const browserScopeText = (slug: string) => `This view reads only the releases of the browser ${slug}; checking a release here reads that release instead.`
const SEARCH_FORM_NAME = 'Search for entities'
const TERMS_LABEL = 'Names or identifiers'
const SEARCH_TAXON_LABEL = 'Organism of the entities to find'
const SEARCH_BUTTON = 'Search'
const RESULTS_NAME = 'Search results'
const CHOSEN_MARK = ' (chosen)'
const matchesName = (term: string) => `Entities matching “${i(term)}”`
const addText = (name: string) => `Add ${i(name)}`
const resolvedChosenText = (term: string, name: string) => `“${i(term)}” is ${i(name)}, among the chosen entities.`
const resolvedNotChosenText = (term: string, name: string) => `“${i(term)}” is ${i(name)}, not among the chosen entities. Press Search to add it.`
const limitText = (max: number) => `The network takes at most ${max} entities. Remove one to add another.`
const resolvedLimitText = (term: string, name: string, max: number) => `“${i(term)}” is ${i(name)}, not added. ${limitText(max)}`
const candidateText = (term: string) => `“${i(term)}” matches one entity, though not as its primary identifier is written. Add it if it is the one you meant.`
const ambiguousText = (term: string) => `“${i(term)}” matches more than one entity. Add the one you meant.`
const chosenText = (name: string) => `${i(name)} is among the chosen entities.`
const unmatchedText = (term: string) => `No entity available to you matches “${i(term)}”.`
const controlTermText = (term: string) => `“${i(term)}” holds a control character, so it was not searched.`
const DERIVED_LEGEND = 'Dashed two-tone outline: an entity reached by following gene-to-protein links from a starting entity (a derived seed). The line under its name gives the type of each link followed.'
const derivationText = (seedName: string, via: string, releaseNames: readonly string[]) => {
  const from = `Reached from ${i(seedName)} ${via}`
  return releaseNames.length === 0 ? `${from}.` : `${from}; stated by ${releaseNames.map((name) => i(name)).join(', ')}.`
}
const BADGE_LINE = 'Synthetic demonstration data Made up to demonstrate openPIP; it describes no real biology.'
const SYNTHETIC_BADGE = 'Synthetic demonstration data'
const HIDDEN_ONE = '1 relationship with tested-and-not-detected evidence is hidden by the outcome filter.'
const PROVISIONAL = "Status: provisional (created from a release's evidence; no reference release has stated this identifier yet)."
const CLOSE = 'Close'
const CAPTION = 'Relationships in view'
const ENTITIES_IN_VIEW = 'Entities in view'
const RELEASES_IN_NETWORK = 'Releases in this network'
const TABLE_REGION_LABEL = 'Table of relationships in view'
const BACK_TO_GALLERY = 'Back to the gallery'
const FACTS_LABEL = 'synthetic construct (taxid 32630); matched by its label'
const FACTS_CASE = 'synthetic construct (taxid 32630); matched by its primary identifier in another case'
const ENCODES_ROW = 'Encodes: SYN-TF1 (Gene) to SYN-P1 (Protein (UniProtKB entry))'
const TF1_G1 = 'Transcriptional regulation: SYN-TF1 (Gene) to SYN-G1 (Gene)'
const TF1_G2 = 'Transcriptional regulation: SYN-TF1 (Gene) to SYN-G2 (Gene)'
const P1_P2 = 'Physical interaction between SYN-P1 (Protein (UniProtKB entry)) and SYN-P2 (Protein (UniProtKB entry))'
const P1_BUTTON = 'SYN-P1 (Protein (UniProtKB entry)), reached via Encodes'
const P1_REGION = 'SYN-P1 (Protein (UniProtKB entry))'
const TF1 = 'SYN-TF1 (Gene)'
const SHARED_PROTEIN = 'SYN-SHARED (Protein (UniProtKB entry))'
const V2 = `${i('Synthetic regulation example')} v2 (synthetic)`
const V1 = `${i('Synthetic regulation example')} v1 (superseded) (synthetic)`
// XD-11's names.
const EDGE_ROWS = [P1_P2, 'Transcriptional regulation: SYN-G1 (Gene) to SYN-TF1 (Gene)', TF1_G1, 'Transcriptional regulation: SYN-TF1 (Gene) to SYN-G4 (Gene)',
  ENCODES_ROW, 'Isoform of: SYN-P1-1 (Protein isoform) to SYN-P1 (Protein (UniProtKB entry))']
const ENTITY_ROWS = ['SYN-G1 (Gene)', 'SYN-G4 (Gene)', 'SYN-TF1 (Gene), seed', P1_BUTTON, 'SYN-P1-1 (Protein isoform), reached via Encodes, then Isoform of',
  'SYN-P2 (Protein (UniProtKB entry))']
// Section 6.1's seventeen terms, in that order.
const MANY_TERMS = ['SYN-G1', 'SYN-G2', 'SYN-G3', 'SYN-G4', 'SYN-G5', 'SYN-G6', 'SYN-TF1', 'SYN-TF2', 'SYN-TF3', 'SYN-P1', 'SYN-P1-1', 'SYN-P2', 'SYN-P3',
  'SYN-ALPHA', 'SYN-SHARED', 'syn-tf1', 'SYN-NOPE']

const STATUS = '/api/graph/v1/status'
const NETWORK = '/api/graph/v1/network'
const NETWORK_TF1 = '/api/graph/v1/network?seeds=entity:7'
const NETWORK_TF1_EXPAND = '/api/graph/v1/network?seeds=entity:7&expand=reference'
const NETWORK_TF1_EXPAND_ALL = '/api/graph/v1/network?seeds=entity:7&expand=reference&outcome=all'
const NETWORK_TF1_EXPAND_V1 = '/api/graph/v1/network?seeds=entity:7&expand=reference&releases=release:1'
const NETWORK_TF1_BROWSER = '/api/graph/v1/network?seeds=entity:7&browser=synthetic-regulation'
const INSTANCE = '/api/graph/v1/browsers/synthetic-regulation'
const RESOLVE_TF1 = '/api/graph/v1/resolve?q=SYN-TF1'
const RESOLVE_SHARED = '/api/graph/v1/resolve?q=SYN-SHARED'
const RESOLVE_TF1_SHARED = '/api/graph/v1/resolve?q=SYN-TF1,SYN-SHARED'
const RESOLVE_ALPHA_CASE = '/api/graph/v1/resolve?q=SYN-ALPHA,syn-tf1'
const RESOLVE_NOPE = '/api/graph/v1/resolve?q=SYN-NOPE'
const RESOLVE_MANY = `/api/graph/v1/resolve?q=${MANY_TERMS.join(',')}`
const ENTITY_7 = '/api/graph/v1/entities/entity%3A7'
const ENTITY_10 = '/api/graph/v1/entities/entity%3A10'
const RELATIONSHIP_1 = '/api/graph/v1/relationships/relationship%3A1'
const EVIDENCE_1 = '/api/graph/v1/relationships/relationship%3A1/evidence'
const RELATIONSHIP_6 = '/api/graph/v1/relationships/relationship%3A6'
const EVIDENCE_6 = '/api/graph/v1/relationships/relationship%3A6/evidence'
const RELATIONSHIP_7 = '/api/graph/v1/relationships/relationship%3A7'
const RELATIONSHIP_7_ALL = '/api/graph/v1/relationships/relationship%3A7?outcome=all'
// F3's heading for a relationship panel whose edge the network does not draw (G/panels/panelWords.ts:189).
const REF_1_REGION = 'Relationship relationship:1'
const REF_7_REGION = 'Relationship relationship:7'
const EVIDENCE_7 = '/api/graph/v1/relationships/relationship%3A7/evidence'

// 12.0's helpers.
const page = () => document.querySelector('main') as HTMLElement
const h1 = (name: string) => screen.getByRole('heading', { level: 1, name })
const networkRegion = () => screen.getByRole('region', { name: NETWORK_HEADING })
const notice = () => networkRegion().querySelector('[role="status"][aria-live="polite"]') as HTMLElement
const rowNames = () => Array.from(within(networkRegion()).getByRole('table', { name: CAPTION }).querySelectorAll('tbody th button'),
  (node) => node.textContent)
const entityButtons = () => within(screen.getByRole('list', { name: ENTITIES_IN_VIEW })).getAllByRole('button')
const tf1Expand = () => recorded<GraphNetworkResponse>('network-tf1-expand').body
/** The recorded expanded SYN-TF1 answer under another echo: a stub's body for a request no recording answers. */
const echoed = (changes: Partial<GraphNetworkQuery>): GraphNetworkResponse => ({ ...tf1Expand(), query: { ...tf1Expand().query, ...changes } })
/** Answers the network requests whose search, as the client sends it, is a key; any other request falls through. */
function serveNetworks(bodies: Readonly<Record<string, JsonBodyType>>) {
  const byQuery = new Map(Object.entries(bodies))
  server.use(http.get(NETWORK, ({ request }) => {
    const body = byQuery.get(new URL(request.url).search)
    return body === undefined ? undefined : HttpResponse.json(body)
  }))
}
const statusWith = (change: (body: GraphStatus) => JsonBodyType) =>
  server.use(http.get(STATUS, () => HttpResponse.json(change(recorded<GraphStatus>('status-on').body))))
/** F4's address check: every address inside the page is http(s), the scheme in any case, or a single-slash router path. */
const strayAddresses = () => Array.from(page().querySelectorAll('a[href]'), (link) => link.getAttribute('href') ?? '')
  .filter((href) => !/^(?:https?:\/\/|\/(?!\/))/i.test(href))
function held() {
  let release: () => void = () => {}
  const promise = new Promise<void>((resolve) => {
    release = resolve
  })
  return { promise, release: () => release() }
}
/** 12.0 item 5: a wait outside act until `ms` have passed. */
const settled = (ms = 400) => {
  const until = performance.now() + ms
  return waitFor(() => expect(performance.now()).toBeGreaterThan(until), { timeout: ms + GRAPH_WAIT.timeout })
}
const pause = (ms: number) => act(async () => {
  await new Promise((resolve) => setTimeout(resolve, ms))
})

// 12.7's helpers.
const searchRegion = () => screen.getByRole('region', { name: SEARCH_HEADING })
const box = () => screen.getByRole('textbox', { name: TERMS_LABEL })
const searchLine = () => within(searchRegion()).getByRole('status')
const termLines = () => Array.from(screen.getByRole('list', { name: RESULTS_NAME }).children, (item) => item.firstElementChild?.textContent ?? '')
const chosen = () => within(screen.getByRole('list', { name: CHOSEN_LABEL })).getAllByRole('listitem').map((item) => item.textContent)
const item = (name: string) => `${name} ${removeText(name)}`
const problemLine = () => screen.getByRole('group', { name: FILTERS_NAME }).nextElementSibling as HTMLElement
const search = (router: GraphRouteRender['router']) => router.state.location.search
const rowsShown = () => waitFor(() => expect(rowNames()).toContain(ENCODES_ROW), GRAPH_WAIT)

const asked = () => requests.urls.map((url) => url.pathname + url.search)
const askedAfter = (n: number) => asked().slice(n)
const following = (first: Node, second: Node) => (first.compareDocumentPosition(second) & Node.DOCUMENT_POSITION_FOLLOWING) !== 0
const inOrder = (nodes: Node[]) => nodes.slice(1).forEach((node, at) => expect(following(nodes[at], node), String(at + 1)).toBe(true))
const badges = () => Array.from(page().querySelectorAll('p')).filter((node) => node.textContent === BADGE_LINE)
const backTexts = () => Array.from(page().querySelectorAll('*')).filter((node) => node.children.length === 0 && (node.textContent ?? '').startsWith('Back to '))
const recordLine = (name: string) => {
  const row = recorded<GraphEvidenceResponse>(name).body.evidence[0]
  return `${row.record.key}, line ${row.record.line_number} (${row.id})`
}
const releaseItems = (list: Element) => within(list as HTMLElement).getAllByRole('listitem').map((entry) => {
  const terms = Array.from(entry.querySelectorAll('dt'), (node) => node.textContent ?? '')
  const lines = Array.from(entry.querySelectorAll('dd'), (node) => node.textContent ?? '')
  return Object.fromEntries(terms.map((term, index) => [term, lines[index]]))
})
const networkReleases = () => screen.getByRole('heading', { level: 3, name: RELEASES_IN_NETWORK }).nextElementSibling as HTMLElement
const answered = (url: string) => waitFor(() => expect(answers.answers.filter((answer) => answer.endsWith(` ${url}`)).length).toBeGreaterThan(0), GRAPH_WAIT)
const askedFor = (url: string) => waitFor(() => expect(asked()).toContain(url), GRAPH_WAIT)

/** Every history write after the render, as [action, search]: "one PUSH to X" is exactly one entry since a mark. */
function track(router: GraphRouteRender['router']) {
  const writes: [string, string][] = []
  let key = router.state.location.key
  router.subscribe((state) => {
    if (state.location.key !== key) {
      key = state.location.key
      writes.push([state.historyAction, state.location.search])
    }
  })
  return writes
}
const wrote = (writes: [string, string][], from: number, action: string, text: string) =>
  waitFor(() => expect(writes.slice(from)).toEqual([[action, text]]), GRAPH_WAIT)

async function open(path: string, options: Parameters<typeof renderGraphRoute>[1] = {}) {
  const view = renderGraphRoute(path, options)
  await screen.findByRole('heading', { level: 1, name: EXPLORE_TITLE }, GRAPH_WAIT)
  return { ...view, writes: track(view.router) }
}

async function searchFor(user: ReturnType<typeof userEvent.setup>, text: string) {
  await user.clear(box())
  await user.click(box())
  await user.paste(text)
  await user.click(screen.getByRole('button', { name: SEARCH_BUTTON }))
}

function recordFocus() {
  const focused: [Element, string][] = []
  const record = (event: FocusEvent) => {
    const target = event.target as Element
    focused.push([target, target.textContent ?? ''])
  }
  document.addEventListener('focusin', record)
  return { focused, stop: () => document.removeEventListener('focusin', record) }
}

describe('the explorer page', () => {
  it('XP-1: with no seed the page shows the search and one sentence and sends nothing', async () => {
    for (const path of ['/explore', '/explore?v=1']) {
      const before = asked().length
      const { router } = await open(path)
      const form = await screen.findByRole('search', { name: SEARCH_FORM_NAME }, GRAPH_WAIT)
      expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1)
      const region = searchRegion()
      expect(within(region).getByRole('heading', { name: SEARCH_HEADING }).tagName).toBe('H2')
      expect(region.contains(form)).toBe(true)
      expect(within(form).getByRole('textbox', { name: TERMS_LABEL })).toBeInTheDocument()
      expect(within(form).getByRole('combobox', { name: 'Entity type' })).toBeInTheDocument()
      expect(within(form).getByRole('combobox', { name: SEARCH_TAXON_LABEL })).toBeInTheDocument()
      expect(within(form).getByRole('button', { name: SEARCH_BUTTON })).toBeInTheDocument()
      const prompt = within(region).getByText(NO_SEED_PROMPT)
      expect(following(form, prompt)).toBe(true)
      expect(screen.queryByRole('region', { name: NETWORK_HEADING })).toBeNull()
      expect(screen.queryByRole('group', { name: TOGGLES_NAME })).toBeNull()
      expect(badges()).toEqual([])
      expect(backTexts()).toEqual([])
      await pause(300)
      expect(askedAfter(before)).toEqual([STATUS])
      expect(search(router)).toBe(path === '/explore' ? '' : '?v=1')
      cleanup()
    }
  }, 20_000)

  it('XP-2: an address with a seed draws the expanded network, its marks and its controls in order', async () => {
    const user = userEvent.setup()
    const { router } = await open('/explore?v=1&seeds=entity%3A7')
    await rowsShown()
    expect(asked()).toEqual([STATUS, NETWORK_TF1_EXPAND])
    expect(search(router)).toBe('?v=1&seeds=entity%3A7')
    expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1)
    expect(badges()).toHaveLength(1)
    expect(badges()[0].firstElementChild?.textContent).toBe(SYNTHETIC_BADGE)
    expect(chosen()).toEqual([item(TF1)])
    const removeAll = screen.getByRole('button', { name: REMOVE_ALL })
    const toggles = screen.getByRole('group', { name: TOGGLES_NAME })
    const follow = within(toggles).getByRole('checkbox', { name: FOLLOW_LABEL })
    const outcome = within(toggles).getByRole('checkbox', { name: OUTCOME_LABEL })
    expect(follow).toBeChecked()
    expect(follow).toHaveAccessibleDescription(FOLLOW_NOTE)
    expect(outcome).not.toBeChecked()
    expect(outcome).toHaveAccessibleDescription(OUTCOME_NOTE)
    const layers = screen.getByRole('group', { name: LAYERS_LEGEND })
    for (const name of ['Physical interactions: 1 shown', 'Physical interaction: 1 shown', 'Protein interaction, type not stated: 0 shown', 'Regulation: 3 shown']) {
      expect(within(layers).getByRole('checkbox', { name })).toBeChecked()
    }
    const filters = screen.getByRole('group', { name: FILTERS_NAME })
    const form = screen.getByRole('search', { name: SEARCH_FORM_NAME })
    expect(within(filters).getByRole('combobox', { name: TAXON_LABEL })).toBeInTheDocument()
    expect(within(form).getByRole('combobox', { name: SEARCH_TAXON_LABEL })).toBeInTheDocument()
    expect(rowNames()).toEqual(EDGE_ROWS)
    expect(entityButtons().map((node) => node.textContent)).toEqual(ENTITY_ROWS)
    expect(Array.from(notice().querySelectorAll('p'), (node) => node.textContent)).toEqual([HIDDEN_ONE])
    expect(screen.getByText(DERIVED_LEGEND)).toBeInTheDocument()
    const items = releaseItems(networkReleases())
    expect(items).toHaveLength(1)
    expect(items[0].Release).toBe('Synthetic regulation example v2')
    expect(items[0].Licence).toBe('MIT License https://github.com/Helmy-Lab/openpip-2.0/blob/main/LICENSE')
    expect(items[0].Attribution).toBe('Copyright (c) 2016-2026 The openPIP authors')
    inOrder([
      h1(EXPLORE_TITLE), badges()[0], screen.getByRole('heading', { level: 2, name: SEARCH_HEADING }), form,
      screen.getByRole('heading', { level: 2, name: NETWORK_HEADING }), within(networkRegion()).getByText(CHOSEN_LABEL, { selector: 'p' }),
      screen.getByRole('list', { name: CHOSEN_LABEL }), removeAll, toggles, layers, filters, problemLine(), notice(),
      screen.getByRole('region', { name: TABLE_REGION_LABEL }), screen.getByRole('heading', { level: 3, name: RELEASES_IN_NETWORK }),
    ])
    expect(h1(EXPLORE_TITLE).parentElement?.style.maxWidth).toBe('1280px')
    expect(form.style.flexWrap).toBe('wrap')
    expect(toggles.style.flexWrap).toBe('wrap')
    expect(filters.style.flexWrap).toBe('wrap')
    expect(layers.style.minWidth).toBe('0px')
    expect(notice().parentElement?.style.flex).toBe('999 1 560px')
    expect(notice().parentElement?.parentElement?.style.flexWrap).toBe('wrap')
    expect(backTexts()).toEqual([])
    await searchFor(user, 'SYN-SHARED')
    const shared = await screen.findByRole('list', { name: matchesName('SYN-SHARED') }, GRAPH_WAIT)
    expect(within(shared).getAllByRole('button').map((node) => node.textContent)).toEqual([addText('SYN-SHARED (Gene)'), addText(SHARED_PROTEIN)])
    expect(badges()).toHaveLength(1)
    expect(following(badges()[0], searchRegion())).toBe(true)
    expect(within(searchRegion()).queryAllByText(BADGE_LINE)).toEqual([])
    expect(asked()).toEqual([STATUS, NETWORK_TF1_EXPAND, RESOLVE_SHARED])
    cleanup()
    const before = asked().length
    const f4 = await open('/explore?seeds=entity:7')
    await rowsShown()
    expect(askedAfter(before)).toEqual([STATUS, NETWORK_TF1_EXPAND])
    expect(chosen()).toEqual([item(TF1)])
    expect(search(f4.router)).toBe('?seeds=entity:7')
  }, 20_000)

  it('XP-3: the two toggles change the request and the address, one history entry per group', async () => {
    const user = userEvent.setup()
    const { router } = await open('/explore?v=1&seeds=entity%3A7')
    await rowsShown()
    const follow = () => screen.getByRole('checkbox', { name: FOLLOW_LABEL })
    await user.click(follow())
    expect(router.state.historyAction).toBe('PUSH')
    expect(search(router)).toBe('?v=1&seeds=entity%3A7&follow=off')
    expect(follow()).not.toBeChecked()
    await askedFor(NETWORK_TF1)
    await waitFor(() => expect(rowNames()).toEqual([
      'Transcriptional regulation: SYN-G1 (Gene) to SYN-TF1 (Gene)', TF1_G1, 'Transcriptional regulation: SYN-TF1 (Gene) to SYN-G4 (Gene)',
    ]), GRAPH_WAIT)
    await user.click(follow())
    expect(router.state.historyAction).toBe('REPLACE')
    expect(search(router)).toBe('?v=1&seeds=entity%3A7')
    expect(follow()).toBeChecked()
    await rowsShown()
    await user.click(follow())
    expect(router.state.historyAction).toBe('REPLACE')
    expect(search(router)).toBe('?v=1&seeds=entity%3A7&follow=off')
    await act(() => router.navigate(-1))
    expect(search(router)).toBe('?v=1&seeds=entity%3A7')
    expect(router.state.location.key).toBe('default')
    await user.click(screen.getByRole('checkbox', { name: OUTCOME_LABEL }))
    expect(router.state.historyAction).toBe('PUSH')
    expect(search(router)).toBe('?v=1&seeds=entity%3A7&outcome=all')
    await askedFor(NETWORK_TF1_EXPAND_ALL)
    await waitFor(() => expect(rowNames()).toContain(TF1_G2), GRAPH_WAIT)
    expect(notice().childElementCount).toBe(0)
    expect(asked()).toEqual([STATUS, NETWORK_TF1_EXPAND, NETWORK_TF1, NETWORK_TF1_EXPAND_ALL])
  }, 20_000)

  it('XP-4: a search adds its resolved entity, a choice adds another, and removals move focus to the box', async () => {
    const user = userEvent.setup()
    const network = held()
    const two = '/api/graph/v1/network?seeds=entity:13,entity:7&expand=reference'
    server.use(http.get(NETWORK, async ({ request }) => {
      const query = new URL(request.url).search
      if (query === '?seeds=entity:7&expand=reference') {
        await network.promise
        return undefined
      }
      if (query === '?seeds=entity:13,entity:7&expand=reference') return HttpResponse.json(echoed({ seeds: ['entity:13', 'entity:7'] }))
      return undefined
    }))
    const { router, writes } = await open('/explore')
    await screen.findByRole('search', { name: SEARCH_FORM_NAME }, GRAPH_WAIT)
    let mark = writes.length
    await searchFor(user, 'SYN-TF1, SYN-SHARED')
    await wrote(writes, mark, 'PUSH', '?v=1&seeds=entity%3A7')
    await askedFor(RESOLVE_TF1_SHARED)
    await waitFor(() => expect(chosen()).toEqual([item(TF1)]), GRAPH_WAIT)
    await waitFor(() => expect(termLines()).toEqual([resolvedChosenText('SYN-TF1', TF1), ambiguousText('SYN-SHARED')]), GRAPH_WAIT)
    expect(searchLine().textContent).toBe('2 terms searched. 1 is among the chosen entities; 1 needs a choice from its list below.')
    const list = screen.getByRole('list', { name: matchesName('SYN-SHARED') })
    const adds = within(list).getAllByRole('button')
    expect(adds.map((node) => node.textContent)).toEqual([addText('SYN-SHARED (Gene)'), addText(SHARED_PROTEIN)])
    for (const add of adds) expect(add.parentElement?.querySelector('p')?.textContent).toBe(FACTS_LABEL)
    expect(new URLSearchParams(search(router)).get('seeds')).toBe('entity:7')
    network.release()
    await answered(NETWORK_TF1_EXPAND)
    await rowsShown()
    expect(rowNames()).toEqual(expect.arrayContaining([P1_P2, TF1_G1]))
    mark = writes.length
    await user.click(screen.getByRole('button', { name: addText(SHARED_PROTEIN) }))
    await wrote(writes, mark, 'PUSH', '?v=1&seeds=entity%3A7%2Centity%3A13')
    const picked = screen.getByRole('button', { name: addText(SHARED_PROTEIN) })
    await waitFor(() => expect(picked).toHaveAttribute('aria-disabled', 'true'), GRAPH_WAIT)
    expect(document.activeElement).toBe(picked)
    expect(picked.nextSibling?.textContent).toBe(CHOSEN_MARK)
    expect(termLines()[1]).toBe(`${ambiguousText('SYN-SHARED')} ${chosenText(SHARED_PROTEIN)}`)
    expect(chosen()).toEqual([item(TF1), item(SHARED_PROTEIN)])
    await askedFor(two)
    await answered(two)
    mark = writes.length
    await user.click(screen.getByRole('button', { name: removeText(SHARED_PROTEIN) }))
    expect(document.activeElement).toBe(box())
    await wrote(writes, mark, 'PUSH', '?v=1&seeds=entity%3A7')
    const before = asked().length
    await settled()
    expect(askedAfter(before)).toEqual([])
    mark = writes.length
    await user.click(screen.getByRole('button', { name: REMOVE_ALL }))
    expect(document.activeElement).toBe(box())
    await wrote(writes, mark, 'PUSH', '?v=1')
    expect(screen.queryByRole('region', { name: NETWORK_HEADING })).toBeNull()
    expect(screen.getByText(NO_SEED_PROMPT)).toBeInTheDocument()
    expect(termLines()[0]).toBe(resolvedNotChosenText('SYN-TF1', TF1))
    expect(asked()).toEqual([STATUS, RESOLVE_TF1_SHARED, NETWORK_TF1_EXPAND, two])
  }, 20_000)

  it('XP-5: ambiguous and candidate terms are never added by themselves, and the search carries the badge while no network is drawn', async () => {
    const user = userEvent.setup()
    const { router, writes } = await open('/explore')
    await screen.findByRole('search', { name: SEARCH_FORM_NAME }, GRAPH_WAIT)
    await searchFor(user, 'SYN-SHARED')
    await waitFor(() => expect(termLines()).toEqual([ambiguousText('SYN-SHARED')]), GRAPH_WAIT)
    expect(within(screen.getByRole('list', { name: matchesName('SYN-SHARED') })).getAllByRole('button').map((node) => node.textContent))
      .toEqual([addText('SYN-SHARED (Gene)'), addText(SHARED_PROTEIN)])
    expect(badges()).toHaveLength(1)
    const badge = badges()[0]
    expect(searchRegion().contains(badge)).toBe(true)
    expect(following(searchLine(), badge) && following(badge, screen.getByRole('list', { name: RESULTS_NAME }))).toBe(true)
    expect(badge.firstElementChild?.textContent).toBe(SYNTHETIC_BADGE)
    await pause(300)
    expect(search(router)).toBe('')
    expect(asked().filter((url) => url.startsWith(NETWORK))).toEqual([])
    await searchFor(user, 'SYN-ALPHA, syn-tf1')
    await waitFor(() => expect(termLines()).toEqual([candidateText('SYN-ALPHA'), candidateText('syn-tf1')]), GRAPH_WAIT)
    const alpha = within(screen.getByRole('list', { name: matchesName('SYN-ALPHA') })).getAllByRole('button')
    expect(alpha.map((node) => node.textContent)).toEqual([addText('SYN-ALPHA (Gene)')])
    expect(alpha[0].parentElement?.querySelector('p')?.textContent).toBe(FACTS_LABEL)
    const tf1 = within(screen.getByRole('list', { name: matchesName('syn-tf1') })).getAllByRole('button')
    expect(tf1.map((node) => node.textContent)).toEqual([addText(TF1)])
    expect(tf1[0].parentElement?.querySelector('p')?.textContent).toBe(FACTS_CASE)
    await pause(300)
    expect(search(router)).toBe('')
    let mark = writes.length
    await user.click(tf1[0])
    await wrote(writes, mark, 'PUSH', '?v=1&seeds=entity%3A7')
    await askedFor(NETWORK_TF1_EXPAND)
    const added = within(screen.getByRole('list', { name: matchesName('syn-tf1') })).getByRole('button')
    await waitFor(() => expect(added).toHaveAttribute('aria-disabled', 'true'), GRAPH_WAIT)
    expect(added).toBeEnabled()
    expect(document.activeElement).toBe(added)
    expect(added.nextSibling?.textContent).toBe(CHOSEN_MARK)
    expect(termLines()[1]).toBe(`${candidateText('syn-tf1')} ${chosenText(TF1)}`)
    await rowsShown()
    expect(badges()).toHaveLength(1)
    expect(following(badges()[0], searchRegion())).toBe(true)
    expect(within(searchRegion()).queryAllByText(BADGE_LINE)).toEqual([])
    const key = router.state.location.key
    await searchFor(user, 'SYN-TF1')
    await waitFor(() => expect(termLines()).toEqual([resolvedChosenText('SYN-TF1', TF1)]), GRAPH_WAIT)
    await pause(300)
    expect(router.state.location.key).toBe(key)
    await searchFor(user, 'SYN-NOPE')
    await waitFor(() => expect(termLines()).toEqual([unmatchedText('SYN-NOPE')]), GRAPH_WAIT)
    expect(searchLine().textContent).toBe('1 term searched. 1 matched no entity available to you.')
    expect(new URLSearchParams(search(router)).get('seeds')).toBe('entity:7')
    mark = asked().length
    await searchFor(user, 'a\u0007b')
    await pause(300)
    expect(askedAfter(mark)).toEqual([])
    expect(termLines()).toEqual([controlTermText('a\u0007b')])
    expect(searchLine().textContent).toBe('No term was searched.')
    expect(asked()).toEqual([STATUS, RESOLVE_SHARED, RESOLVE_ALPHA_CASE, NETWORK_TF1_EXPAND, RESOLVE_TF1, RESOLVE_NOPE])
  }, 20_000)

  it('XP-6: a many-term search adds the first ten in typed order and words the rest', async () => {
    const user = userEvent.setup()
    const ten = ['entity:1', 'entity:10', 'entity:2', 'entity:3', 'entity:4', 'entity:5', 'entity:6', 'entity:7', 'entity:8', 'entity:9']
    const network = `/api/graph/v1/network?seeds=${ten.join(',')}&expand=reference`
    serveNetworks({ [`?seeds=${ten.join(',')}&expand=reference`]: echoed({ seeds: ten }) as unknown as JsonBodyType })
    const { writes } = await open('/explore')
    await screen.findByRole('search', { name: SEARCH_FORM_NAME }, GRAPH_WAIT)
    const mark = writes.length
    await searchFor(user, MANY_TERMS.join(','))
    const typed = Array.from({ length: 10 }, (_, index) => `entity:${index + 1}`)
    await wrote(writes, mark, 'PUSH', `?v=1&seeds=${encodeURIComponent(typed.join(','))}`)
    await waitFor(() => expect(searchLine().textContent).toBe(
      '17 terms searched. 10 are among the chosen entities; 3 were not added: the network takes no more; 3 need a choice from their lists below; 1 matched no entity available to you.',
    ), GRAPH_WAIT)
    const lines = termLines()
    expect(lines[10]).toBe(resolvedLimitText('SYN-P1-1', 'SYN-P1-1 (Protein isoform)', 10))
    expect(lines[11]).toBe(resolvedLimitText('SYN-P2', 'SYN-P2 (Protein (UniProtKB entry))', 10))
    expect(lines[12]).toBe(resolvedLimitText('SYN-P3', SHARED_PROTEIN, 10))
    expect(lines[13]).toBe(`${candidateText('SYN-ALPHA')} ${limitText(10)}`)
    expect(lines[14]).toBe(`${ambiguousText('SYN-SHARED')} ${limitText(10)}`)
    expect(lines[15]).toBe(`${candidateText('syn-tf1')} ${limitText(10)}`)
    expect(lines[16]).toBe(unmatchedText('SYN-NOPE'))
    const tf1 = within(screen.getByRole('list', { name: matchesName('syn-tf1') })).getByRole('button')
    expect(tf1).toHaveAttribute('aria-disabled', 'true')
    expect(tf1.nextSibling?.textContent).toBe(CHOSEN_MARK)
    await answered(network)
    await waitFor(() => expect(chosen()).toEqual(['SYN-G1 (Gene)', 'SYN-G2 (Gene)', 'SYN-ALPHA (Gene)', 'SYN-G4 (Gene)', 'SYN-G5 (Gene)', 'SYN-SHARED (Gene)',
      TF1, 'SYN-TF2 (Gene)', 'SYN-TF3 (Gene)', 'SYN-P1 (Protein (UniProtKB entry))'].map(item)), GRAPH_WAIT)
    expect(asked()).toEqual([STATUS, RESOLVE_MANY, network])
  }, 20_000)

  it('XP-7: with the search off the address\'s seeds are still drawn, and with none the page says nothing is drawn', async () => {
    const user = userEvent.setup()
    statusWith((body) => ({ ...body, features: { ...body.features, resolve: false } }))
    const { writes } = await open('/explore?v=1&seeds=entity%3A7')
    const region = await screen.findByRole('region', { name: SEARCH_HEADING }, GRAPH_WAIT)
    expect(within(region).getByText(SEARCH_UNAVAILABLE)).toBeInTheDocument()
    expect(screen.queryByRole('search', { name: SEARCH_FORM_NAME })).toBeNull()
    expect(screen.queryByText(NO_SEED_PROMPT)).toBeNull()
    await rowsShown()
    expect(chosen()).toEqual([item(TF1)])
    expect(asked()).toEqual([STATUS, NETWORK_TF1_EXPAND])
    const mark = writes.length
    await user.click(screen.getByRole('button', { name: REMOVE_ALL }))
    expect(document.activeElement).toBe(screen.getByRole('heading', { level: 2, name: SEARCH_HEADING }))
    await wrote(writes, mark, 'PUSH', '?v=1')
    expect(screen.queryByRole('region', { name: NETWORK_HEADING })).toBeNull()
    expect(within(searchRegion()).getByText(SEARCH_UNAVAILABLE_EMPTY)).toBeInTheDocument()
    expect(screen.queryByText(SEARCH_UNAVAILABLE)).toBeNull()
    expect(screen.queryByText(NO_SEED_PROMPT)).toBeNull()
    cleanup()
    const before = asked().length
    await open('/explore')
    expect(await screen.findByText(SEARCH_UNAVAILABLE_EMPTY, {}, GRAPH_WAIT)).toBeInTheDocument()
    expect(screen.queryByText(SEARCH_UNAVAILABLE)).toBeNull()
    expect(screen.queryByRole('search', { name: SEARCH_FORM_NAME })).toBeNull()
    expect(screen.queryByText(NO_SEED_PROMPT)).toBeNull()
    expect(askedAfter(before)).toEqual([STATUS])
  }, 20_000)

  const UNUSABLE: [string, string, string, string][] = [
    ['?v=2&seeds=entity%3A7', NEWER_VERSION, NEWER_VERSION_LINK, '/explore'],
    ['?v=&seeds=entity%3A7', UNREADABLE_VERSION, NEWER_VERSION_LINK, '/explore'],
    ['?v=1&v=1&seeds=entity%3A7', UNREADABLE_VERSION, NEWER_VERSION_LINK, '/explore'],
    ['?v=1&seeds=entity%3A0&utm_source=news', 'The explorer cannot use the chosen entities in this address.', 'Show the explorer without the chosen entities',
      '/explore?v=1&utm_source=news'],
    ['?v=1&seeds=entity%3A7&releases=release%3A1&browser=x', 'The explorer cannot use the releases and browser in this address.',
      'Show the explorer without the releases and browser', '/explore?v=1&seeds=entity%3A7'],
    ['?seeds=entity%3A7&follow=on&taxon=0', 'The explorer cannot use the gene-to-protein setting and partner organism in this address.',
      'Show the explorer without the gene-to-protein setting and partner organism', '/explore?seeds=entity%3A7'],
    ['?v=1&seeds=entity%3A7&selected=edge%3A3', 'The explorer cannot use the selection in this address.', 'Show the explorer without the selection',
      '/explore?v=1&seeds=entity%3A7'],
  ]
  it.each(UNUSABLE)('XP-8: an address the explorer cannot use sends nothing and links to a usable one (%s)', async (address, sentence, link, href) => {
    const user = userEvent.setup()
    const { router } = await open(`/explore${address}`)
    expect(await screen.findByText(sentence, {}, GRAPH_WAIT)).toBeInTheDocument()
    const anchor = screen.getByRole('link', { name: link })
    expect(anchor).toHaveAttribute('href', href)
    await pause(300)
    expect(asked()).toEqual([STATUS])
    expect(screen.queryByRole('region', { name: SEARCH_HEADING })).toBeNull()
    expect(search(router)).toBe(address)
    const after: Record<string, string> = { '?v=2&seeds=entity%3A7': '', '?v=&seeds=entity%3A7': '', '?v=1&seeds=entity%3A0&utm_source=news': '?v=1&utm_source=news' }
    if (address in after) {
      await user.click(anchor)
      expect(document.activeElement).toBe(h1(EXPLORE_TITLE))
      expect(router.state.historyAction).toBe('PUSH')
      expect(search(router)).toBe(after[address])
      expect(await screen.findByRole('search', { name: SEARCH_FORM_NAME }, GRAPH_WAIT)).toBeInTheDocument()
      expect(screen.getByText(NO_SEED_PROMPT)).toBeInTheDocument()
      expect(document.activeElement).toBe(h1(EXPLORE_TITLE))
    }
  }, 20_000)

  it('XP-9: unknown names are kept and never sent', async () => {
    const user = userEvent.setup()
    const { router, writes } = await open('/explore?expand=reference&seeds=entity%3A7&utm_source=news')
    await rowsShown()
    expect(asked()).toEqual([STATUS, NETWORK_TF1_EXPAND])
    expect(search(router)).toBe('?expand=reference&seeds=entity%3A7&utm_source=news')
    const mark = writes.length
    await user.click(screen.getByRole('checkbox', { name: FOLLOW_LABEL }))
    await wrote(writes, mark, 'PUSH', '?v=1&seeds=entity%3A7&follow=off&expand=reference&utm_source=news')
    await askedFor(NETWORK_TF1)
    await answered(NETWORK_TF1)
    expect(asked()).toEqual([STATUS, NETWORK_TF1_EXPAND, NETWORK_TF1])
  }, 20_000)

  it('XP-10: layer boxes change the request, and a 400 naming layers or expand is worded and removable once', async () => {
    const user = userEvent.setup()
    const layered = '/api/graph/v1/network?seeds=entity:7&layers=transcriptional_regulation&expand=reference'
    const body = echoed({ layers: ['transcriptional_regulation'] })
    serveNetworks({
      '?seeds=entity:7&layers=transcriptional_regulation&expand=reference': {
        ...body, edges: body.edges.filter((edge) => edge.id !== 'relationship:3'), nodes: body.nodes.filter((node) => node.id !== 'entity:12'),
      } as unknown as JsonBodyType,
    })
    const { router } = await open('/explore?v=1&seeds=entity%3A7')
    await rowsShown()
    await user.click(screen.getByRole('checkbox', { name: 'Physical interactions: 1 shown' }))
    expect(router.state.historyAction).toBe('PUSH')
    expect(search(router)).toBe('?v=1&seeds=entity%3A7&layers=transcriptional_regulation')
    await answered(layered)
    for (const name of ['Physical interactions: off', 'Physical interaction: off', 'Protein interaction, type not stated: off']) {
      expect(await screen.findByRole('checkbox', { name }, GRAPH_WAIT)).not.toBeChecked()
    }
    const regulation = await screen.findByRole('checkbox', { name: 'Regulation: 3 shown' }, GRAPH_WAIT)
    expect(regulation).toBeChecked()
    expect(regulation).toHaveAttribute('aria-disabled', 'true')
    expect(screen.getByText(LAYERS_MINIMUM)).toBeInTheDocument()
    expect(rowNames()).toContain(ENCODES_ROW)
    await user.click(screen.getByRole('checkbox', { name: 'Physical interactions: off' }))
    expect(router.state.historyAction).toBe('REPLACE')
    expect(search(router)).toBe('?v=1&seeds=entity%3A7')
    let before = asked().length
    await settled()
    expect(askedAfter(before)).toEqual([])
    await act(() => router.navigate(-1))
    expect(router.state.location.key).toBe('default')
    cleanup()
    const detail = 'layers names a reference type; expand follows reference links itself.'
    server.use(http.get(NETWORK, ({ request }) => (new URL(request.url).search === '?seeds=entity:7&layers=encodes&expand=reference'
      ? HttpResponse.json({ detail, errors: [{ parameter: 'layers', value: 'encodes', allowed: null, limit: null }] }, { status: 400 })
      : undefined)))
    const second = await open('/explore?v=1&seeds=entity%3A7&layers=encodes')
    await answered('/api/graph/v1/network?seeds=entity:7&layers=encodes&expand=reference')
    expect(within(screen.getByRole('group', { name: LAYERS_LEGEND })).getByRole('checkbox', { name: 'Encodes' })).toBeChecked()
    await waitFor(() => expect(problemLine().textContent).toBe('The network cannot use the layers of this view.'), GRAPH_WAIT)
    const removal = screen.getByRole('button', { name: 'Remove the layers from this view' })
    expect(following(problemLine(), removal)).toBe(true)
    expect(page().textContent ?? '').not.toContain(detail)
    let mark = second.writes.length
    await user.click(removal)
    expect(document.activeElement).toBe(screen.getByRole('heading', { level: 2, name: NETWORK_HEADING }))
    expect(problemLine().textContent).toBe('')
    expect(screen.queryByRole('button', { name: 'Remove the layers from this view' })).toBeNull()
    await wrote(second.writes, mark, 'PUSH', '?v=1&seeds=entity%3A7')
    await answered(NETWORK_TF1_EXPAND)
    await rowsShown()
    expect(problemLine().textContent).toBe('')
    expect(screen.queryByRole('button', { name: 'Remove the layers from this view' })).toBeNull()
    expect(page().textContent ?? '').not.toContain(detail)
    cleanup()
    const unknown = "Unknown parameter 'expand'. Allowed: seeds."
    server.use(http.get(NETWORK, ({ request }) => (new URL(request.url).search === '?seeds=entity:7&expand=reference'
      ? HttpResponse.json({ detail: unknown, errors: [{ parameter: 'expand', value: null, allowed: ['seeds'], limit: null }] }, { status: 400 })
      : undefined)))
    before = asked().length
    const third = await open('/explore?v=1&seeds=entity%3A7')
    await waitFor(() => expect(problemLine().textContent).toBe('The network cannot use the gene-to-protein setting of this view.'), GRAPH_WAIT)
    const off = screen.getByRole('button', { name: 'Remove the gene-to-protein setting from this view' })
    mark = third.writes.length
    await user.click(off)
    expect(document.activeElement).toBe(screen.getByRole('heading', { level: 2, name: NETWORK_HEADING }))
    await wrote(third.writes, mark, 'PUSH', '?v=1&seeds=entity%3A7&follow=off')
    await askedFor(NETWORK_TF1)
    await answered(NETWORK_TF1)
    await waitFor(() => expect(problemLine().textContent).toBe(''), GRAPH_WAIT)
    expect(page().textContent ?? '').not.toContain(unknown)
    expect(askedAfter(before)).toEqual([STATUS, NETWORK_TF1_EXPAND, NETWORK_TF1])
  }, 20_000)

  const DEEP: [string, string, JsonBodyType | null, () => void, boolean][] = [
    ['&direction=out', `${NETWORK_TF1_EXPAND}&direction=out`, echoed({ direction: 'out' }) as unknown as JsonBodyType,
      () => expect((screen.getByRole('combobox', { name: DIRECTION_LABEL }) as HTMLSelectElement).selectedOptions[0].textContent).toBe('Outgoing'), true],
    ['&effect=activating', `${NETWORK_TF1_EXPAND}&effect=activating`, echoed({ effect: ['activating'] }) as unknown as JsonBodyType,
      () => expect(screen.getByRole('checkbox', { name: 'activates' })).toBeChecked(), true],
    ['&effect=not_stated', `${NETWORK_TF1_EXPAND}&effect=not_stated`, echoed({ effect: ['not_stated'] }) as unknown as JsonBodyType,
      () => expect(screen.getByRole('checkbox', { name: 'no effect stated' })).toBeChecked(), true],
    ['&evidence_basis=experimental', `${NETWORK_TF1_EXPAND}&evidence_basis=experimental`, echoed({ evidence_basis: ['experimental'] }) as unknown as JsonBodyType,
      () => expect(screen.getByRole('checkbox', { name: 'experimental' })).toBeChecked(), true],
    ['&score_types=synthetic%3Aconfidence', `${NETWORK_TF1_EXPAND}&score=synthetic:confidence::`, echoed({ score: ['synthetic:confidence::'] } as Partial<GraphNetworkQuery>) as unknown as JsonBodyType,
      () => expect(screen.getByRole('checkbox', { name: 'Synthetic confidence' })).toBeChecked(), true],
    ['&taxon=9606', `${NETWORK_TF1_EXPAND}&taxon=9606`, echoed({ taxon: 9606 }) as unknown as JsonBodyType,
      () => expect((screen.getByRole('combobox', { name: TAXON_LABEL }) as HTMLSelectElement).selectedOptions[0].textContent).toBe('Homo sapiens (taxid 9606)'), true],
    ['&releases=release%3A1', NETWORK_TF1_EXPAND_V1, null, () => {
      expect(screen.getByRole('checkbox', { name: V1 })).toBeChecked()
      expect(within(screen.getByRole('group', { name: RELEASES_LEGEND })).getByText(RELEASES_PINNED)).toBeInTheDocument()
    }, true],
    ['&browser=synthetic-regulation', `${NETWORK_TF1_EXPAND}&browser=synthetic-regulation`, echoed({ browser: 'synthetic-regulation' }) as unknown as JsonBodyType, () => {
      expect(screen.getByText(browserScopeText('synthetic-regulation'))).toBeInTheDocument()
      expect(screen.getByRole('checkbox', { name: V2 })).not.toBeChecked()
    }, true],
    ['&layers=transcriptional_regulation', '/api/graph/v1/network?seeds=entity:7&layers=transcriptional_regulation&expand=reference',
      echoed({ layers: ['transcriptional_regulation'] }) as unknown as JsonBodyType,
      () => expect(screen.getByRole('checkbox', { name: 'Physical interactions: off' })).not.toBeChecked(), false],
    ['method: /explore?v=1&seeds=entity%3A980&method=MI%3A0018', '/api/graph/v1/network?seeds=entity:980&expand=reference&method=MI:0018',
      methodFilterNetwork as unknown as JsonBodyType, () => {
        expect((screen.getByRole('textbox', { name: METHOD_LABEL }) as HTMLInputElement).value).toBe('MI:0018')
        expect(rowNames()).toEqual([
          'Physical interaction between SYN-N981 (Protein (UniProtKB entry)) and SYN-N983 (Protein (UniProtKB entry))',
          'Encodes: SYN-N980 (Gene) to SYN-N981 (Protein (UniProtKB entry))',
          'Isoform of: SYN-N981-1 (Protein isoform) to SYN-N981 (Protein (UniProtKB entry))',
        ])
      }, true],
  ]
  it.each(DEEP)('XP-11: each filter sends its exact request (%s)', async (address, request, body, control, clear) => {
    if (body !== null) serveNetworks({ [request.slice(NETWORK.length)]: body })
    await open(address.startsWith('method') ? '/explore?v=1&seeds=entity%3A980&method=MI%3A0018' : `/explore?v=1&seeds=entity%3A7${address}`)
    await answered(request)
    expect(asked()).toEqual([STATUS, request])
    await waitFor(control, GRAPH_WAIT)
    expect(screen.queryByRole('button', { name: CLEAR_FILTERS }) !== null).toBe(clear)
  }, 20_000)

  it('XP-12: filter writes push or replace, and Clear filters and a release box that goes move focus first', async () => {
    const user = userEvent.setup()
    const out = `${NETWORK_TF1_EXPAND}&direction=out`
    const inward = `${NETWORK_TF1_EXPAND}&direction=in`
    const release2 = '/api/graph/v1/network?seeds=entity:7&expand=reference&releases=release:2'
    const method = '/api/graph/v1/network?seeds=entity:7&expand=reference&method=MI:0018&releases=release:2'
    serveNetworks({
      [out.slice(NETWORK.length)]: echoed({ direction: 'out' }) as unknown as JsonBodyType,
      [inward.slice(NETWORK.length)]: echoed({ direction: 'in' }) as unknown as JsonBodyType,
      [release2.slice(NETWORK.length)]: echoed({ releases: ['release:2'] }) as unknown as JsonBodyType,
      [method.slice(NETWORK.length)]: echoed({ method: ['MI:0018'], releases: ['release:2'] }) as unknown as JsonBodyType,
    })
    const { router } = await open('/explore?v=1&seeds=entity%3A7')
    await rowsShown()
    const direction = () => screen.getByRole('combobox', { name: DIRECTION_LABEL })
    await user.selectOptions(direction(), 'Outgoing')
    expect(router.state.historyAction).toBe('PUSH')
    expect(search(router)).toBe('?v=1&seeds=entity%3A7&direction=out')
    await answered(out)
    expect(document.activeElement).toBe(direction())
    await user.selectOptions(direction(), 'Incoming')
    expect(router.state.historyAction).toBe('REPLACE')
    expect(search(router)).toBe('?v=1&seeds=entity%3A7&direction=in')
    await answered(inward)
    await act(() => router.navigate(-1))
    expect(router.state.location.key).toBe('default')
    await rowsShown()
    await user.click(await screen.findByRole('checkbox', { name: V2 }, GRAPH_WAIT))
    expect(router.state.historyAction).toBe('PUSH')
    expect(search(router)).toBe('?v=1&seeds=entity%3A7&releases=release%3A2')
    await answered(release2)
    expect(await screen.findByText(RELEASES_PINNED, {}, GRAPH_WAIT)).toBeInTheDocument()
    await user.type(screen.getByRole('textbox', { name: METHOD_LABEL }), 'mi:0018')
    await user.click(screen.getByRole('button', { name: METHOD_APPLY }))
    expect(router.state.historyAction).toBe('PUSH')
    expect(search(router)).toBe('?v=1&seeds=entity%3A7&method=MI%3A0018&releases=release%3A2')
    await answered(method)
    expect((screen.getByRole('textbox', { name: METHOD_LABEL }) as HTMLInputElement).value).toBe('MI:0018')
    await user.click(screen.getByRole('button', { name: CLEAR_FILTERS }))
    expect(document.activeElement).toBe(direction())
    expect(router.state.historyAction).toBe('PUSH')
    expect(search(router)).toBe('?v=1&seeds=entity%3A7')
    expect(screen.queryByRole('button', { name: CLEAR_FILTERS })).toBeNull()
    const before = asked().length
    await settled()
    expect(askedAfter(before)).toEqual([])
    expect(asked()).toEqual([STATUS, NETWORK_TF1_EXPAND, out, inward, release2, method])
    cleanup()
    const mark = asked().length
    const second = await open('/explore?v=1&seeds=entity%3A7&releases=release%3A1')
    await answered(NETWORK_TF1_EXPAND_V1)
    await user.click(await screen.findByRole('checkbox', { name: V1 }, GRAPH_WAIT))
    expect(second.router.state.historyAction).toBe('PUSH')
    expect(search(second.router)).toBe('?v=1&seeds=entity%3A7')
    await answered(NETWORK_TF1_EXPAND)
    await screen.findByRole('checkbox', { name: V2 }, GRAPH_WAIT)
    expect(screen.queryByRole('checkbox', { name: V1 })).toBeNull()
    expect(document.activeElement).toBe(screen.getByRole('group', { name: RELEASES_LEGEND }))
    expect(askedAfter(mark)).toEqual([STATUS, NETWORK_TF1_EXPAND_V1, NETWORK_TF1_EXPAND])
  }, 20_000)

  it('XP-13: the instance\'s action opens the explorer with a way back that survives its writes', async () => {
    const user = userEvent.setup()
    const { router } = renderGraphRoute('/browsers/synthetic-regulation?seeds=entity%3A7', { state: { gallerySearch: '?tag=gene_regulation' } })
    await screen.findByRole('region', { name: NETWORK_HEADING }, GRAPH_WAIT)
    const action = screen.getByRole('link', { name: EXPLORE_ACTION })
    expect(action).toHaveAttribute('href', '/explore?v=1&seeds=entity%3A7')
    await user.click(action)
    const back = { pathname: '/browsers/synthetic-regulation', search: '?seeds=entity%3A7', title: 'Synthetic regulation example', state: { gallerySearch: '?tag=gene_regulation' } }
    expect(router.state.location.pathname).toBe('/explore')
    expect(router.state.location.search).toBe('?v=1&seeds=entity%3A7')
    expect(router.state.location.state).toEqual({ exploreBack: back })
    const heading = await screen.findByRole('heading', { level: 1, name: EXPLORE_TITLE }, GRAPH_WAIT)
    await waitFor(() => expect(document.activeElement).toBe(heading), GRAPH_WAIT)
    const link = screen.getByRole('link', { name: backText('Synthetic regulation example') })
    expect(link).toHaveAttribute('href', '/browsers/synthetic-regulation?seeds=entity%3A7')
    expect(following(heading, link) && following(link, searchRegion())).toBe(true)
    await rowsShown()
    await user.click(screen.getByRole('checkbox', { name: FOLLOW_LABEL }))
    expect(router.state.location.state).toEqual({ exploreBack: back, exploreControl: 'follow' })
    expect(screen.getByRole('link', { name: backText('Synthetic regulation example') })).toBeInTheDocument()
    await askedFor(NETWORK_TF1)
    await user.click(screen.getByRole('link', { name: backText('Synthetic regulation example') }))
    expect(router.state.location.pathname).toBe('/browsers/synthetic-regulation')
    expect(router.state.location.search).toBe('?seeds=entity%3A7')
    expect(router.state.location.state).toEqual({ gallerySearch: '?tag=gene_regulation' })
    expect(await screen.findByRole('link', { name: BACK_TO_GALLERY }, GRAPH_WAIT)).toHaveAttribute('href', '/browsers?tag=gene_regulation')
    expect(asked()).toEqual([STATUS, INSTANCE, NETWORK_TF1_BROWSER, NETWORK_TF1_EXPAND, NETWORK_TF1])
    cleanup()
    await open('/explore?v=1&seeds=entity%3A7', { state: { exploreBack: { pathname: '/explore', search: '', title: 'x', state: null } } })
    await rowsShown()
    expect(backTexts()).toEqual([])
  }, 20_000)

  it('XP-14: the closure\'s drop and a truncation are announced in the notice', async () => {
    const drop = '/api/graph/v1/network?seeds=entity:980&expand=reference'
    const network = held()
    server.use(http.get(NETWORK, async ({ request }) => {
      if (new URL(request.url).search !== '?seeds=entity:980&expand=reference') return undefined
      await network.promise
      return HttpResponse.json(closureDropNetwork as unknown as JsonBodyType)
    }))
    await open('/explore?v=1&seeds=entity%3A980')
    await waitFor(() => expect(notice()).not.toBeNull(), GRAPH_WAIT)
    expect(notice().textContent).toBe('')
    network.release()
    await waitFor(() => expect(notice().querySelector('p')?.textContent).toBe(
      'At least 6 entities reached through gene-to-protein links were left out: a network looks up at most 26 entities, the chosen and the reached together.',
    ), GRAPH_WAIT)
    expect(page().textContent ?? '').not.toContain('6 derived seeds omitted')
    expect(asked()).toEqual([STATUS, drop])
    cleanup()
    let before = asked().length
    statusWith((body) => {
      const limits: Record<string, unknown> = { ...body.limits }
      delete limits.max_expanded_seeds
      return { ...body, limits } as unknown as JsonBodyType
    })
    await open('/explore?v=1&seeds=entity%3A980')
    await waitFor(() => expect(notice().querySelector('p')?.textContent).toBe(
      'At least 6 entities reached through gene-to-protein links were left out to keep the network within its limits.',
    ), GRAPH_WAIT)
    expect(askedAfter(before)).toEqual([STATUS, drop])
    cleanup()
    server.resetHandlers()
    const truncated = recorded<GraphNetworkResponse>('network-truncated').body
    serveNetworks({ '?seeds=entity:9&expand=reference': { ...truncated, query: { ...truncated.query, expand: 'reference' } } as unknown as JsonBodyType })
    before = asked().length
    await open('/explore?v=1&seeds=entity%3A9')
    await waitFor(() => expect(notice().textContent).toBe(
      '1 relationship shown; 3 more that matched were left out to keep the network within its size limits (the relationship limit).',
    ), GRAPH_WAIT)
    expect(askedAfter(before)).toEqual([STATUS, '/api/graph/v1/network?seeds=entity:9&expand=reference'])
  }, 20_000)

  it('XP-15: a visitor sees an empty view', async () => {
    const user = userEvent.setup()
    server.use(...typedGraphVisitorHandlers)
    const { router } = await open('/explore?v=1&seeds=entity%3A7')
    await waitFor(() => expect(answers.answers).toContain(`200 ${NETWORK_TF1_EXPAND}`), GRAPH_WAIT)
    await waitFor(() => expect(chosen()).toEqual([item('entity:7')]), GRAPH_WAIT)
    expect(badges()).toEqual([])
    expect(screen.queryByRole('heading', { level: 3, name: RELEASES_IN_NETWORK })).toBeNull()
    await searchFor(user, 'SYN-TF1')
    await waitFor(() => expect(answers.answers).toContain(`200 ${RESOLVE_TF1}`), GRAPH_WAIT)
    await waitFor(() => expect(termLines()).toEqual([unmatchedText('SYN-TF1')]), GRAPH_WAIT)
    expect(search(router)).toBe('?v=1&seeds=entity%3A7')
  }, 20_000)

  it('XP-16: a 404 from a switched-off portal rechecks status once', async () => {
    const user = userEvent.setup()
    await open('/explore?v=1&seeds=entity%3A7')
    await rowsShown()
    server.use(...typedGraphOffHandlers)
    let before = asked().length
    await user.click(screen.getByRole('checkbox', { name: FOLLOW_LABEL }))
    expect(await screen.findByText(EXPLORE_NOT_ENABLED, {}, GRAPH_WAIT)).toBeInTheDocument()
    expect(h1(EXPLORE_TITLE)).toBeInTheDocument()
    await answered(STATUS)
    expect(askedAfter(before)).toEqual([NETWORK_TF1, STATUS])
    cleanup()
    server.resetHandlers()
    await open('/explore')
    await screen.findByRole('search', { name: SEARCH_FORM_NAME }, GRAPH_WAIT)
    server.use(...typedGraphOffHandlers)
    before = asked().length
    await searchFor(user, 'SYN-TF1')
    expect(await screen.findByText(EXPLORE_NOT_ENABLED, {}, GRAPH_WAIT)).toBeInTheDocument()
    expect(askedAfter(before)).toEqual([RESOLVE_TF1, STATUS])
  }, 20_000)

  it('XP-17: a selection replaces the address and adds no history entry, and removing the last seed drops it', async () => {
    const user = userEvent.setup()
    const { router } = renderGraphRoute('/')
    await act(() => router.navigate('/explore?v=1&seeds=entity%3A7'))
    await rowsShown()
    const opener = entityButtons().find((node) => node.textContent === P1_BUTTON) as HTMLElement
    await user.click(opener)
    expect(router.state.historyAction).toBe('REPLACE')
    expect(search(router)).toBe('?v=1&seeds=entity%3A7&selected=entity%3A10')
    const region = await screen.findByRole('region', { name: P1_REGION }, GRAPH_WAIT)
    const heading = within(region).getByRole('heading', { name: P1_REGION })
    expect(heading.tagName).toBe('H3')
    await waitFor(() => expect(document.activeElement).toBe(heading), GRAPH_WAIT)
    expect(region.querySelector('p')?.textContent).toBe(derivationText(TF1, 'via Encodes', ['Synthetic regulation example v2']))
    await askedFor(ENTITY_10)
    await within(region).findByText(PROVISIONAL, {}, GRAPH_WAIT)
    await user.click(within(region).getByRole('button', { name: CLOSE }))
    expect(router.state.historyAction).toBe('REPLACE')
    expect(search(router)).toBe('?v=1&seeds=entity%3A7')
    expect(screen.queryByRole('region', { name: P1_REGION })).toBeNull()
    await waitFor(() => expect(document.activeElement).toBe(entityButtons().find((node) => node.textContent === P1_BUTTON)), GRAPH_WAIT)
    const row = screen.getAllByRole('button', { name: ENCODES_ROW }).find((node) => node.closest('table') !== null) as HTMLElement
    await user.click(row)
    expect(router.state.historyAction).toBe('REPLACE')
    expect(search(router)).toBe('?v=1&seeds=entity%3A7&selected=relationship%3A1')
    const join = await screen.findByRole('region', { name: ENCODES_ROW }, GRAPH_WAIT)
    await askedFor(EVIDENCE_1)
    expect(await within(join).findByText(recordLine('evidence-encodes'), {}, GRAPH_WAIT)).toBeInTheDocument()
    await act(() => router.navigate(-1))
    expect(router.state.location.pathname).toBe('/')
    expect(asked()).toEqual([STATUS, NETWORK_TF1_EXPAND, ENTITY_10, RELATIONSHIP_1, EVIDENCE_1])
    cleanup()
    const second = await open('/explore')
    await screen.findByRole('search', { name: SEARCH_FORM_NAME }, GRAPH_WAIT)
    await searchFor(user, 'SYN-TF1')
    await rowsShown()
    await user.click(entityButtons().find((node) => node.textContent === P1_BUTTON) as HTMLElement)
    expect(second.router.state.historyAction).toBe('REPLACE')
    expect(search(second.router)).toBe('?v=1&seeds=entity%3A7&selected=entity%3A10')
    await waitFor(() => expect(asked().filter((url) => url === ENTITY_10)).toHaveLength(2), GRAPH_WAIT)
    let mark = second.writes.length
    await user.click(screen.getByRole('button', { name: REMOVE_ALL }))
    await wrote(second.writes, mark, 'PUSH', '?v=1')
    expect(document.activeElement).toBe(box())
    mark = second.writes.length
    await searchFor(user, 'SYN-TF1')
    await wrote(second.writes, mark, 'PUSH', '?v=1&seeds=entity%3A7')
    await rowsShown()
    await settled()
    expect(screen.queryByRole('region', { name: P1_REGION })).toBeNull()
    expect(document.activeElement).toBe(screen.getByRole('button', { name: SEARCH_BUTTON }))
    await user.click(entityButtons().find((node) => node.textContent === P1_BUTTON) as HTMLElement)
    expect(second.router.state.historyAction).toBe('REPLACE')
    expect(search(second.router)).toBe('?v=1&seeds=entity%3A7&selected=entity%3A10')
    mark = second.writes.length
    await user.click(screen.getByRole('button', { name: removeText(TF1) }))
    await wrote(second.writes, mark, 'PUSH', '?v=1')
  }, 20_000)

  it('XP-18: an address\'s selection opens its panel once the network answers, one the answer lacks opens with the view\'s releases, and its focus is the panel\'s', async () => {
    const user = userEvent.setup()
    // (a) A join restored, then sequence B.
    const a = await open('/explore?v=1&seeds=entity%3A7&selected=relationship%3A1')
    const join = await screen.findByRole('region', { name: ENCODES_ROW }, GRAPH_WAIT)
    await waitFor(() => expect(asked()).toEqual([STATUS, NETWORK_TF1_EXPAND, RELATIONSHIP_1, EVIDENCE_1]), GRAPH_WAIT)
    expect(asked().filter((url) => url.includes('expand'))).toEqual([NETWORK_TF1_EXPAND])
    await waitFor(() => expect(document.activeElement).toBe(within(join).getByRole('heading', { name: ENCODES_ROW })), GRAPH_WAIT)
    await user.click(screen.getByRole('checkbox', { name: FOLLOW_LABEL }))
    expect(a.router.state.historyAction).toBe('PUSH')
    expect(search(a.router)).toBe('?v=1&seeds=entity%3A7&follow=off&selected=relationship%3A1')
    await askedFor(NETWORK_TF1)
    await waitFor(() => expect(screen.queryByRole('region', { name: ENCODES_ROW })).toBeNull(), GRAPH_WAIT)
    await user.click(screen.getAllByRole('button', { name: TF1_G1 }).find((node) => node.closest('table') !== null) as HTMLElement)
    expect(a.router.state.historyAction).toBe('REPLACE')
    expect(search(a.router)).toBe('?v=1&seeds=entity%3A7&follow=off&selected=relationship%3A6')
    expect(await screen.findByRole('region', { name: TF1_G1 }, GRAPH_WAIT)).toBeInTheDocument()
    await askedFor(EVIDENCE_6)
    expect(asked()).toEqual(expect.arrayContaining([RELATIONSHIP_6, EVIDENCE_6]))
    await answered(EVIDENCE_6)
    await act(() => a.router.navigate(-1))
    expect(search(a.router)).toBe('?v=1&seeds=entity%3A7&selected=relationship%3A1')
    expect(await screen.findByRole('region', { name: ENCODES_ROW }, GRAPH_WAIT)).toBeInTheDocument()
    cleanup()
    // (b) An entity restored, first as a document's first load, then as a client-side arrival.
    let focus = recordFocus()
    let before = asked().length
    try {
      await open('/explore?v=1&seeds=entity%3A7&selected=entity%3A10')
      const region = await screen.findByRole('region', { name: P1_REGION }, GRAPH_WAIT)
      await waitFor(() => expect(askedAfter(before)).toEqual([STATUS, NETWORK_TF1_EXPAND, ENTITY_10]), GRAPH_WAIT)
      const heading = within(region).getByRole('heading', { level: 3 })
      expect(focus.focused).toHaveLength(1)
      expect(focus.focused[0][0]).toBe(heading)
      expect(focus.focused[0][1]).toBe('Entity entity:10')
      expect(document.activeElement).toBe(heading)
      expect(scrolls).not.toHaveBeenCalled()
      await within(region).findByText(PROVISIONAL, {}, GRAPH_WAIT)
      await user.click(within(region).getByRole('button', { name: CLOSE }))
      expect(document.activeElement).toBe(document.body)
    } finally {
      focus.stop()
    }
    cleanup()
    const arrival = renderGraphRoute('/')
    focus = recordFocus()
    try {
      await act(() => arrival.router.navigate('/explore?v=1&seeds=entity%3A7&selected=entity%3A10'))
      const region = await screen.findByRole('region', { name: P1_REGION }, GRAPH_WAIT)
      const heading = within(region).getByRole('heading', { level: 3 })
      await waitFor(() => expect(focus.focused).toHaveLength(2), GRAPH_WAIT)
      expect(focus.focused[0][0]).toBe(h1(EXPLORE_TITLE))
      expect(focus.focused[1][0]).toBe(heading)
      expect(scrolls).not.toHaveBeenCalled()
      await within(region).findByText(PROVISIONAL, {}, GRAPH_WAIT)
      await user.click(within(region).getByRole('button', { name: CLOSE }))
      expect(document.activeElement).toBe(h1(EXPLORE_TITLE))
    } finally {
      focus.stop()
    }
    cleanup()
    // (c) A ranked edge keeps the echo's outcome.
    before = asked().length
    await open('/explore?v=1&seeds=entity%3A7&outcome=all&selected=relationship%3A7')
    expect(await screen.findByRole('region', { name: TF1_G2 }, GRAPH_WAIT)).toBeInTheDocument()
    await waitFor(() => expect(askedAfter(before)).toEqual([STATUS, NETWORK_TF1_EXPAND_ALL, RELATIONSHIP_7_ALL, EVIDENCE_7]), GRAPH_WAIT)
    await answered(EVIDENCE_7)
    cleanup()
    // Sequence A: a restored relationship the answer lacks opens as a panel opened from another panel does, with the
    // view's releases and no filter, and the next write keeps it (8.8 rule 1).
    before = asked().length
    const seqA = await open('/explore?v=1&seeds=entity%3A7&selected=relationship%3A7')
    await rowsShown()
    expect(await screen.findByRole('region', { name: REF_7_REGION }, GRAPH_WAIT)).toBeInTheDocument()
    await waitFor(() => expect(askedAfter(before)).toEqual([STATUS, NETWORK_TF1_EXPAND, RELATIONSHIP_7, EVIDENCE_7]), GRAPH_WAIT)
    await answered(EVIDENCE_7)
    await user.click(screen.getByRole('checkbox', { name: FOLLOW_LABEL }))
    expect(search(seqA.router)).toBe('?v=1&seeds=entity%3A7&follow=off&selected=relationship%3A7')
    await askedFor(NETWORK_TF1)
    await answered(NETWORK_TF1)
    cleanup()
    // Sequence C.
    before = asked().length
    const seqC = await open('/explore?v=1&seeds=entity%3A7&follow=off')
    await waitFor(() => expect(rowNames()).toContain(TF1_G1), GRAPH_WAIT)
    await user.click(entityButtons().find((node) => node.textContent === 'SYN-TF1 (Gene), seed') as HTMLElement)
    expect(seqC.router.state.historyAction).toBe('REPLACE')
    expect(search(seqC.router)).toBe('?v=1&seeds=entity%3A7&follow=off&selected=entity%3A7')
    await askedFor(ENTITY_7)
    const reference = await screen.findByRole('button', { name: ENCODES_ROW }, GRAPH_WAIT)
    await user.click(reference)
    expect(seqC.router.state.historyAction).toBe('REPLACE')
    expect(search(seqC.router)).toBe('?v=1&seeds=entity%3A7&follow=off&selected=relationship%3A1')
    expect(await screen.findByRole('region', { name: ENCODES_ROW }, GRAPH_WAIT)).toBeInTheDocument()
    await askedFor(EVIDENCE_1)
    await answered(EVIDENCE_1)
    await settled()
    expect(screen.getByRole('region', { name: ENCODES_ROW })).toBeInTheDocument()
    expect(askedAfter(before)).toEqual([STATUS, NETWORK_TF1, ENTITY_7, RELATIONSHIP_1, EVIDENCE_1])
  }, 40_000)

  it('XP-19: Back and Forward restore the selection from the cache, and Back reopens a panel the hook cleared', async () => {
    const user = userEvent.setup()
    const { router } = await open('/explore?v=1&seeds=entity%3A7&selected=entity%3A10')
    await screen.findByRole('region', { name: P1_REGION }, GRAPH_WAIT)
    await askedFor(ENTITY_10)
    await rowsShown()
    await user.click(screen.getByRole('checkbox', { name: OUTCOME_LABEL }))
    expect(router.state.historyAction).toBe('PUSH')
    expect(search(router)).toBe('?v=1&seeds=entity%3A7&outcome=all&selected=entity%3A10')
    await askedFor(NETWORK_TF1_EXPAND_ALL)
    await waitFor(() => expect(rowNames()).toContain(TF1_G2), GRAPH_WAIT)
    await user.click(screen.getAllByRole('button', { name: ENCODES_ROW }).find((node) => node.closest('table') !== null) as HTMLElement)
    expect(router.state.historyAction).toBe('REPLACE')
    expect(search(router)).toBe('?v=1&seeds=entity%3A7&outcome=all&selected=relationship%3A1')
    const join = await screen.findByRole('region', { name: ENCODES_ROW }, GRAPH_WAIT)
    await askedFor(EVIDENCE_1)
    expect(asked()).toEqual(expect.arrayContaining([RELATIONSHIP_1, EVIDENCE_1]))
    await within(join).findByText(recordLine('evidence-encodes'), {}, GRAPH_WAIT)
    const n = asked().length
    await act(() => router.navigate(-1))
    expect(search(router)).toBe('?v=1&seeds=entity%3A7&selected=entity%3A10')
    const entity = await screen.findByRole('region', { name: P1_REGION }, GRAPH_WAIT)
    await within(entity).findByText(PROVISIONAL, {}, GRAPH_WAIT)
    await waitFor(() => expect(rowNames()).not.toContain(TF1_G2), GRAPH_WAIT)
    await act(() => router.navigate(1))
    expect(search(router)).toBe('?v=1&seeds=entity%3A7&outcome=all&selected=relationship%3A1')
    const again = await screen.findByRole('region', { name: ENCODES_ROW }, GRAPH_WAIT)
    await within(again).findByText(recordLine('evidence-encodes'), {}, GRAPH_WAIT)
    await waitFor(() => expect(rowNames()).toContain(TF1_G2), GRAPH_WAIT)
    expect(askedAfter(n)).toEqual([])
    cleanup()
    // Sequence D.
    const d = await open('/explore?v=1&seeds=entity%3A7&outcome=all')
    await waitFor(() => expect(rowNames()).toContain(TF1_G2), GRAPH_WAIT)
    await user.click(screen.getAllByRole('button', { name: TF1_G2 }).find((node) => node.closest('table') !== null) as HTMLElement)
    expect(d.router.state.historyAction).toBe('REPLACE')
    expect(search(d.router)).toBe('?v=1&seeds=entity%3A7&outcome=all&selected=relationship%3A7')
    expect(await screen.findByRole('region', { name: TF1_G2 }, GRAPH_WAIT)).toBeInTheDocument()
    await askedFor(EVIDENCE_7)
    expect(asked()).toEqual(expect.arrayContaining([RELATIONSHIP_7_ALL, EVIDENCE_7]))
    await answered(EVIDENCE_7)
    await user.click(screen.getByRole('checkbox', { name: OUTCOME_LABEL }))
    expect(d.router.state.historyAction).toBe('PUSH')
    expect(search(d.router)).toBe('?v=1&seeds=entity%3A7&selected=relationship%3A7')
    await answered(NETWORK_TF1_EXPAND)
    await waitFor(() => expect(screen.queryByRole('region', { name: TF1_G2 })).toBeNull(), GRAPH_WAIT)
    await act(() => d.router.navigate(-1))
    expect(search(d.router)).toBe('?v=1&seeds=entity%3A7&outcome=all&selected=relationship%3A7')
    expect(await screen.findByRole('region', { name: TF1_G2 }, GRAPH_WAIT)).toBeInTheDocument()
  }, 20_000)

  it('XP-20: labels and release titles that hold addresses stay text, and every address in the page is a router path or an http(s) one', async () => {
    const body = JSON.parse(JSON.stringify(tf1Expand())) as GraphNetworkResponse
    for (const node of body.nodes) {
      if (node.id === 'entity:7') node.label = 'javascript:alert(1)'
      if (node.id === 'entity:10') node.label = '//evil.example/x'
    }
    body.releases[0].dataset.title = '//evil.example/x'
    body.releases[0].version = 'javascript:alert(1)'
    body.releases[0].licence = { ...body.releases[0].licence, url: 'javascript:alert(1)' }
    serveNetworks({ '?seeds=entity:7&expand=reference': body as unknown as JsonBodyType })
    renderGraphRoute('/explore?v=1&seeds=entity%3A7&selected=entity%3A10', {
      state: { exploreBack: { pathname: '/browsers/synthetic-regulation', search: '?seeds=entity%3A7', title: 'javascript:alert(1)', state: null } },
    })
    await waitFor(() => expect(rowNames()).toContain('Encodes: javascript:alert(1) (Gene) to //evil.example/x (Protein (UniProtKB entry))'), GRAPH_WAIT)
    const region = await screen.findByRole('region', { name: '//evil.example/x (Protein (UniProtKB entry))' }, GRAPH_WAIT)
    await within(region).findByText(PROVISIONAL, {}, GRAPH_WAIT)
    expect(chosen()).toEqual([item('javascript:alert(1) (Gene)')])
    const items = releaseItems(networkReleases())
    expect(items[0].Release).toBe('//evil.example/x javascript:alert(1)')
    expect(items[0].Licence).toBe('MIT License javascript:alert(1)')
    expect(networkReleases().querySelectorAll('a')).toHaveLength(0)
    expect(screen.getByRole('link', { name: backText('javascript:alert(1)') })).toHaveAttribute('href', '/browsers/synthetic-regulation?seeds=entity%3A7')
    expect(strayAddresses()).toEqual([])
    expect(Array.from(page().querySelectorAll('a[href]'), (link) => link.getAttribute('href') ?? '')
      .filter((href) => href.includes('javascript:') || href.includes('evil.example'))).toEqual([])
    expect(asked()).toEqual([STATUS, NETWORK_TF1_EXPAND, ENTITY_10])
  }, 20_000)

  it('XP-21: an address written from a panel opened in another panel reopens that panel when loaded afresh, and the next write keeps it', async () => {
    const user = userEvent.setup()
    // Sequence C's address (XP-18), as a recipient opens it: the panel is headed by its ref until the network draws the
    // edge, and its body names the ends.
    const { router } = await open('/explore?v=1&seeds=entity%3A7&follow=off&selected=relationship%3A1')
    const join = await screen.findByRole('region', { name: REF_1_REGION }, GRAPH_WAIT)
    await waitFor(() => expect(asked()).toEqual([STATUS, NETWORK_TF1, RELATIONSHIP_1, EVIDENCE_1]), GRAPH_WAIT)
    await within(join).findByText(recordLine('evidence-encodes'), {}, GRAPH_WAIT)
    await user.click(screen.getByRole('checkbox', { name: FOLLOW_LABEL }))
    expect(router.state.historyAction).toBe('PUSH')
    expect(search(router)).toBe('?v=1&seeds=entity%3A7&selected=relationship%3A1')
    await answered(NETWORK_TF1_EXPAND)
    expect(await screen.findByRole('region', { name: ENCODES_ROW }, GRAPH_WAIT)).toBeInTheDocument()
    expect(asked()).toEqual([STATUS, NETWORK_TF1, RELATIONSHIP_1, EVIDENCE_1, NETWORK_TF1_EXPAND])
  }, 20_000)
})

