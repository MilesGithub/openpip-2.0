import { act, cleanup, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { Core, SingularElementArgument } from 'cytoscape'
import { http, HttpResponse } from 'msw'
import type { JsonBodyType } from 'msw'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { sharedEntryNetwork } from '../../mocks/fixtures/typedGraph/extras'
import { typedGraphVisitorHandlers } from '../../mocks/handlers/typedGraph'
import { server } from '../../mocks/server'
import { useAuthStore } from '../../store/authStore'
import { GRAPH_WAIT, recordRequests, recorded } from '../../test/graphHooks'
import type { RequestLog } from '../../test/graphHooks'
import { recordAnswers, renderGraphRoute } from '../../test/graphRoutes'
import type { GraphEvidenceResponse, GraphNetworkQuery, GraphNetworkResponse } from '../graph/types'
import { isolateText } from '../graph/words/isolateText'

// X1 spec 12.8 (XC): /explore through the headless canvas.
const log = vi.hoisted(() => ({ instances: [], runs: new Map() }))
vi.mock('react-cytoscapejs', async (importOriginal) =>
  (await import('../../test/headlessCytoscape')).headlessCytoscapeModule(importOriginal, log))

let requests: RequestLog
let answers: { answers: string[]; stop: () => void }

beforeAll(async () => {
  await import('./ExplorePage')
}, 60_000)

beforeEach(() => {
  // Cleared first, so that every Cytoscape instance in the log is the row's own.
  log.instances.length = 0
  log.runs.clear()
  useAuthStore.getState().logout()
  requests = recordRequests()
  answers = recordAnswers()
  vi.spyOn(console, 'warn')
  vi.spyOn(console, 'error')
  // jsdom prints "Not implemented" for a call no spy replaces, which no console spy sees; no row here reads the calls.
  vi.spyOn(window, 'scrollTo').mockImplementation(() => {})
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

// Words as 12.7's: X1's spec sections 8.10 and 9.3, and F2's, F3's and F4's words of 12.0, never from the modules under test.
const NETWORK_HEADING = 'Network'
const FOLLOW_LABEL = 'Follow gene-to-protein links'
const FILTERS_NAME = 'Filters'
const FOLLOW_COST = 'Following gene-to-protein links adds the entities they reach to the lookups; turn it off to ask for a smaller network.'
const NETWORK_AGAIN = 'Press Try again to ask for this network again.'
const TRY_AGAIN = 'Try again'
const DERIVED_LEGEND = 'Dashed two-tone outline: an entity reached by following gene-to-protein links from a starting entity (a derived seed). The line under its name gives the type of each link followed.'
const derivationText = (seedName: string, via: string, releaseNames: readonly string[]) => {
  const from = `Reached from ${isolateText(seedName)} ${via}`
  return releaseNames.length === 0 ? `${from}.` : `${from}; stated by ${releaseNames.map((name) => isolateText(name)).join(', ')}.`
}
const BADGE_LINE = 'Synthetic demonstration data Made up to demonstrate openPIP; it describes no real biology.'
const LOADING_ENTITY = 'Loading this entity…'
const NOT_LISTED = 'Under the current filters a network would not list this relationship: none of its results in view has a requested outcome.'
const SUPERSEDED = 'superseded: a newer release has since corrected it'
const CAPTION = 'Relationships in view'
const RELEASES_IN_NETWORK = 'Releases in this network'
const ENCODES_ROW = 'Encodes: SYN-TF1 (Gene) to SYN-P1 (Protein (UniProtKB entry))'
const ISOFORM_ROW = 'Isoform of: SYN-P1-1 (Protein isoform) to SYN-P1 (Protein (UniProtKB entry))'

const PATH = '/explore?v=1&seeds=entity%3A7'
const STATUS = '/api/graph/v1/status'
const NETWORK = '/api/graph/v1/network'
const NETWORK_TF1 = '/api/graph/v1/network?seeds=entity:7'
const NETWORK_TF1_EXPAND = '/api/graph/v1/network?seeds=entity:7&expand=reference'
const NETWORK_TF1_EXPAND_V1 = '/api/graph/v1/network?seeds=entity:7&expand=reference&releases=release:1'
const ENTITY_11 = '/api/graph/v1/entities/entity%3A11'
const RELATIONSHIP_1 = '/api/graph/v1/relationships/relationship%3A1'
const EVIDENCE_1 = '/api/graph/v1/relationships/relationship%3A1/evidence'
const RELATIONSHIP_2 = '/api/graph/v1/relationships/relationship%3A2'
const EVIDENCE_2 = '/api/graph/v1/relationships/relationship%3A2/evidence'

const page = () => document.querySelector('main') as HTMLElement
const networkRegion = () => screen.getByRole('region', { name: NETWORK_HEADING })
const notice = () => networkRegion().querySelector('[role="status"][aria-live="polite"]') as HTMLElement
const rowNames = () => Array.from(within(networkRegion()).getByRole('table', { name: CAPTION }).querySelectorAll('tbody th button'),
  (node) => node.textContent)
const rowsShown = () => waitFor(() => expect(rowNames()).toContain(ENCODES_ROW), GRAPH_WAIT)
const problemLine = () => screen.getByRole('group', { name: FILTERS_NAME }).nextElementSibling as HTMLElement
const tf1Expand = () => recorded<GraphNetworkResponse>('network-tf1-expand').body
const echoed = (changes: Partial<GraphNetworkQuery>): GraphNetworkResponse => ({ ...tf1Expand(), query: { ...tf1Expand().query, ...changes } })
function serveNetworks(bodies: Readonly<Record<string, JsonBodyType>>) {
  const byQuery = new Map(Object.entries(bodies))
  server.use(http.get(NETWORK, ({ request }) => {
    const body = byQuery.get(new URL(request.url).search)
    return body === undefined ? undefined : HttpResponse.json(body)
  }))
}
function held() {
  let release: () => void = () => {}
  const promise = new Promise<void>((resolve) => {
    release = resolve
  })
  return { promise, release: () => release() }
}
const settled = (ms = 400) => {
  const until = performance.now() + ms
  return waitFor(() => expect(performance.now()).toBeGreaterThan(until), { timeout: ms + GRAPH_WAIT.timeout })
}
/** The Cytoscape instances still alive; a row taps only the one live instance (BR/composition.test.tsx:112). */
const live = () => (log.instances as Core[]).filter((cy) => !cy.destroyed())
const one = async () => {
  await waitFor(() => expect(live()).toHaveLength(1), GRAPH_WAIT)
  return live()[0]
}
const tap = (cy: Core, id: string) => act(() => {
  cy.$id(id).emit('tap')
})
/** F2's canvas line (BR/composition.test.tsx:160-167). */
const canvasLine = () => notice().nextElementSibling as HTMLElement
const ids = (collection: { map<T>(fn: (element: SingularElementArgument) => T): T[] }) => collection.map((element) => element.id()).sort()
const asked = () => requests.urls.map((url) => url.pathname + url.search)
const askedFor = (url: string) => waitFor(() => expect(asked()).toContain(url), GRAPH_WAIT)
const answered = (url: string) => waitFor(() => expect(answers.answers.filter((answer) => answer.endsWith(` ${url}`)).length).toBeGreaterThan(0), GRAPH_WAIT)
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
const badges = () => Array.from(page().querySelectorAll('p')).filter((node) => node.textContent === BADGE_LINE)
const tryAgains = () => within(networkRegion()).queryAllByRole('button', { name: TRY_AGAIN })

describe('the explorer through the headless canvas', () => {
  it('XC-1: gene, entry and isoform are three nodes joined only by labelled reference links', async () => {
    renderGraphRoute(PATH)
    await rowsShown()
    const cy = await one()
    expect(ids(cy.nodes())).toEqual(['entity:1', 'entity:10', 'entity:11', 'entity:12', 'entity:4', 'entity:7'])
    expect(['entity:7', 'entity:10', 'entity:11'].map((id) => cy.$id(id).data('type'))).toEqual(['gene', 'protein', 'protein_isoform'])
    const three = new Set(['entity:7', 'entity:10', 'entity:11'])
    const joins = cy.edges().filter((edge) => three.has(edge.source().id()) && three.has(edge.target().id()))
    expect(ids(joins)).toEqual(['relationship:1', 'relationship:2'])
    expect([cy.$id('relationship:1').source().id(), cy.$id('relationship:1').target().id()]).toEqual(['entity:7', 'entity:10'])
    expect([cy.$id('relationship:2').source().id(), cy.$id('relationship:2').target().id()]).toEqual(['entity:11', 'entity:10'])
    for (const id of ['relationship:1', 'relationship:2']) expect(cy.$id(id).hasClass('fam-reference'), id).toBe(true)
    expect(cy.$id('relationship:1').data('label')).toBe('Encodes · Synthetic regulation example v2')
    expect(cy.$id('relationship:2').data('label')).toBe('Isoform of · Synthetic regulation example v2')
    expect([cy.$id('relationship:3').source().id(), cy.$id('relationship:3').target().id()].sort()).toEqual(['entity:10', 'entity:12'])
    for (const id of ['entity:10', 'entity:11']) expect([cy.$id(id).hasClass('derived'), cy.$id(id).hasClass('seed')], id).toEqual([true, false])
    expect([cy.$id('entity:7').hasClass('seed'), cy.$id('entity:7').hasClass('derived')]).toEqual([true, false])
    expect([cy.$id('entity:12').hasClass('seed'), cy.$id('entity:12').hasClass('derived')]).toEqual([false, false])
    expect([cy.$id('entity:10').data('label'), cy.$id('entity:11').data('label')]).toEqual(['SYN-P1\nvia Encodes', 'SYN-P1-1\nvia Encodes, then Isoform of'])
    expect(cy.$id('entity:10').style('border-style')).toBe('dashed')
    expect(cy.$id('entity:7').style('border-style')).toBe('solid')
    expect(within(networkRegion()).getByRole('img', { name: 'Network of 6 entities and 6 relationships.' })).toBeInTheDocument()
    expect(within(networkRegion()).getByText(DERIVED_LEGEND)).toBeInTheDocument()
  }, 20_000)

  it('XC-2: a pinned superseded release flags the joins', async () => {
    renderGraphRoute('/explore?v=1&seeds=entity%3A7&releases=release%3A1')
    await rowsShown()
    expect(asked()).toEqual([STATUS, NETWORK_TF1_EXPAND_V1])
    const cy = await one()
    expect([cy.$id('relationship:1').data('label'), cy.$id('relationship:2').data('label')])
      .toEqual(['Encodes · Synthetic regulation example v1 (superseded)', 'Isoform of · Synthetic regulation example v1 (superseded)'])
    expect(releaseItems(networkReleases())[0].Status).toBe(SUPERSEDED)
    expect(badges()).toHaveLength(1)
  }, 20_000)

  it("XC-3: a tapped derived seed's panel states its path", async () => {
    const user = userEvent.setup()
    const { router } = renderGraphRoute(PATH)
    await rowsShown()
    const cy = await one()
    await tap(cy, 'entity:11')
    expect(router.state.historyAction).toBe('REPLACE')
    expect(router.state.location.search).toBe('?v=1&seeds=entity%3A7&selected=entity%3A11')
    const region = await screen.findByRole('region', { name: 'SYN-P1-1 (Protein isoform)' }, GRAPH_WAIT)
    expect(region.querySelector('h1, h2, h3, h4, h5, h6')?.tagName).toBe('H3')
    const loading = await within(region).findByText(LOADING_ENTITY, {}, GRAPH_WAIT)
    const path = within(region).getByText(derivationText('SYN-TF1 (Gene)', 'via Encodes, then Isoform of', ['Synthetic regulation example v2']))
    expect(path.compareDocumentPosition(loading) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    await waitFor(() => expect(cy.$id('entity:11').hasClass('is-selected')).toBe(true), GRAPH_WAIT)
    await askedFor(ENTITY_11)
    await answered(ENTITY_11)
    await user.keyboard('{Escape}')
    await waitFor(() => expect(screen.queryByRole('region', { name: 'SYN-P1-1 (Protein isoform)' })).toBeNull(), GRAPH_WAIT)
    expect(router.state.historyAction).toBe('REPLACE')
    expect(router.state.location.search).toBe('?v=1&seeds=entity%3A7')
    await waitFor(() => expect(cy.$id('entity:11').hasClass('is-selected')).toBe(false), GRAPH_WAIT)
    cleanup()
    renderGraphRoute('/explore?v=1&seeds=entity%3A7&selected=entity%3A10')
    await rowsShown()
    const restored = await one()
    await waitFor(() => expect(restored.$id('entity:10').hasClass('is-selected')).toBe(true), GRAPH_WAIT)
    await answered('/api/graph/v1/entities/entity%3A10')
  }, 20_000)

  it("XC-4: a join's panel sends no filter and shows its evidence", async () => {
    const user = userEvent.setup()
    renderGraphRoute(PATH)
    await rowsShown()
    let cy = await one()
    await tap(cy, 'relationship:2')
    const region = await screen.findByRole('region', { name: ISOFORM_ROW }, GRAPH_WAIT)
    await askedFor(EVIDENCE_2)
    expect(asked()).toEqual([STATUS, NETWORK_TF1_EXPAND, RELATIONSHIP_2, EVIDENCE_2])
    expect(await within(region).findByText(recordLine('evidence-isoform'), {}, GRAPH_WAIT)).toBeInTheDocument()
    expect(asked().filter((url) => url.includes('expand'))).toEqual([NETWORK_TF1_EXPAND])
    cleanup()
    const filtered = '/api/graph/v1/network?seeds=entity:7&expand=reference&evidence_basis=experimental'
    serveNetworks({ '?seeds=entity:7&expand=reference&evidence_basis=experimental': echoed({ evidence_basis: ['experimental'] }) as unknown as JsonBodyType })
    const physical = '/api/graph/v1/relationships/relationship%3A3?evidence_basis=experimental'
    const physicalEvidence = '/api/graph/v1/relationships/relationship%3A3/evidence?evidence_basis=experimental'
    const stubs: Record<string, string> = { [physical]: 'relationship-physical', [physicalEvidence]: 'evidence-physical' }
    const answer = ({ request }: { request: Request }) => {
      const url = new URL(request.url)
      const name = stubs[url.pathname + url.search]
      return name === undefined ? undefined : HttpResponse.json(recorded<JsonBodyType>(name).body)
    }
    server.use(http.get('/api/graph/v1/relationships/:ref', answer), http.get('/api/graph/v1/relationships/:ref/evidence', answer))
    const before = asked().length
    renderGraphRoute('/explore?v=1&seeds=entity%3A7&evidence_basis=experimental')
    await rowsShown()
    cy = await one()
    await tap(cy, 'relationship:1')
    const join = await screen.findByRole('region', { name: ENCODES_ROW }, GRAPH_WAIT)
    await askedFor(EVIDENCE_1)
    expect(asked().slice(before)).toEqual([STATUS, filtered, RELATIONSHIP_1, EVIDENCE_1])
    await within(join).findByText(recordLine('evidence-encodes'), {}, GRAPH_WAIT)
    expect(join.textContent ?? '').not.toContain(NOT_LISTED)
    await user.keyboard('{Escape}')
    await waitFor(() => expect(screen.queryByRole('region', { name: ENCODES_ROW })).toBeNull(), GRAPH_WAIT)
    await tap(cy, 'relationship:3')
    await askedFor(physicalEvidence)
    expect(asked().slice(before)).toEqual([STATUS, filtered, RELATIONSHIP_1, EVIDENCE_1, physical, physicalEvidence])
    await within(screen.getByRole('region', { name: 'Physical interaction between SYN-P1 (Protein (UniProtKB entry)) and SYN-P2 (Protein (UniProtKB entry))' }))
      .findByText(recordLine('evidence-physical'), {}, GRAPH_WAIT)
  }, 20_000)

  it('XC-5: a shared address gives the same requests and element ids in a fresh client', async () => {
    const first = renderGraphRoute(PATH)
    await rowsShown()
    const cy = await one()
    const once = asked()
    const nodes = ids(cy.nodes())
    const edges = ids(cy.edges())
    first.unmount()
    renderGraphRoute(PATH)
    await rowsShown()
    const fresh = await one()
    expect(asked().slice(once.length)).toEqual(once)
    expect(ids(fresh.nodes())).toEqual(nodes)
    expect(ids(fresh.edges())).toEqual(edges)
  }, 20_000)

  it("XC-6: a visitor's canvas says there is nothing to show", async () => {
    server.use(...typedGraphVisitorHandlers)
    renderGraphRoute(PATH)
    await waitFor(() => expect(answers.answers).toContain(`200 ${NETWORK_TF1_EXPAND}`), GRAPH_WAIT)
    await waitFor(() => expect(canvasLine().textContent).toBe('No relationships to show.'), GRAPH_WAIT)
    expect(live()).toEqual([])
    expect(badges()).toEqual([])
  }, 20_000)

  it('XC-7: a failed network keeps F2\'s canvas words, names the follow toggle for a 503, and offers Try again where a repeat can change it', async () => {
    const user = userEvent.setup()
    const failWith = (query: string, response: () => Response) => server.use(http.get(NETWORK, ({ request }) =>
      (new URL(request.url).search === query ? response() : undefined)))
    // A JSON 503.
    failWith('?seeds=entity:7&expand=reference', () => HttpResponse.json({ detail: 'x' }, { status: 503 }))
    renderGraphRoute(PATH)
    await answered(NETWORK_TF1_EXPAND)
    await waitFor(() => expect(canvasLine().textContent).toBe('This network is too large to compute. Narrow the seeds, layers or filters.'), GRAPH_WAIT)
    expect(problemLine().textContent).toBe(FOLLOW_COST)
    expect(tryAgains()).toEqual([])
    expect(asked()).toEqual([STATUS, NETWORK_TF1_EXPAND])
    cleanup()
    failWith('?seeds=entity:7', () => HttpResponse.json({ detail: 'x' }, { status: 503 }))
    let before = asked().length
    renderGraphRoute('/explore?v=1&seeds=entity%3A7&follow=off')
    await answered(NETWORK_TF1)
    await waitFor(() => expect(canvasLine().textContent).toBe('This network is too large to compute. Narrow the seeds, layers or filters.'), GRAPH_WAIT)
    expect(problemLine().textContent).toBe('')
    expect(tryAgains()).toEqual([])
    expect(asked().slice(before)).toEqual([STATUS, NETWORK_TF1])
    cleanup()
    server.resetHandlers()
    // A JSON 500.
    failWith('?seeds=entity:7&expand=reference', () => HttpResponse.json({ detail: 'x' }, { status: 500 }))
    before = asked().length
    renderGraphRoute(PATH)
    await waitFor(() => expect(canvasLine().textContent).toBe('The network could not be loaded.'), GRAPH_WAIT)
    await waitFor(() => expect(problemLine().textContent).toBe(NETWORK_AGAIN), GRAPH_WAIT)
    expect(problemLine().nextElementSibling).toBe(screen.getByRole('button', { name: TRY_AGAIN }))
    expect(tryAgains()).toHaveLength(1)
    expect(asked().slice(before)).toEqual([STATUS, NETWORK_TF1_EXPAND])
    cleanup()
    server.resetHandlers()
    // A JSON 429, then a held answer.
    const network = held()
    let calls = 0
    server.use(http.get(NETWORK, async ({ request }) => {
      if (new URL(request.url).search !== '?seeds=entity:7&expand=reference') return undefined
      calls += 1
      if (calls === 1) return HttpResponse.json({ detail: 't' }, { status: 429, headers: { 'Retry-After': '7' } })
      await network.promise
      return HttpResponse.json(recorded<JsonBodyType>('network-tf1-expand').body)
    }))
    before = asked().length
    renderGraphRoute(PATH)
    await waitFor(() => expect(canvasLine().textContent).toBe('Too many requests. Try again in 7 seconds.'), GRAPH_WAIT)
    await waitFor(() => expect(problemLine().textContent).toBe(NETWORK_AGAIN), GRAPH_WAIT)
    const button = screen.getByRole('button', { name: TRY_AGAIN })
    expect(problemLine().nextElementSibling).toBe(button)
    expect(asked().slice(before)).toEqual([STATUS, NETWORK_TF1_EXPAND])
    await user.click(button)
    await waitFor(() => expect(asked().slice(before)).toEqual([STATUS, NETWORK_TF1_EXPAND, NETWORK_TF1_EXPAND]), GRAPH_WAIT)
    await waitFor(() => expect(button).toHaveAttribute('aria-disabled', 'true'), GRAPH_WAIT)
    expect(button).toBeEnabled()
    expect(document.activeElement).toBe(button)
    network.release()
    await rowsShown()
    await waitFor(() => expect(tryAgains()).toEqual([]), GRAPH_WAIT)
    expect(problemLine().textContent).toBe('')
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole('heading', { level: 2, name: NETWORK_HEADING })), GRAPH_WAIT)
    expect(asked().slice(before)).toEqual([STATUS, NETWORK_TF1_EXPAND, NETWORK_TF1_EXPAND])
    await settled()
  }, 20_000)

  it('XC-8: with follow off the view is plain depth 1', async () => {
    const user = userEvent.setup()
    renderGraphRoute(PATH)
    await rowsShown()
    await one()
    await user.click(screen.getByRole('checkbox', { name: FOLLOW_LABEL }))
    await askedFor(NETWORK_TF1)
    await answered(NETWORK_TF1)
    await waitFor(() => expect(ids(live()[0].nodes())).toEqual(['entity:1', 'entity:4', 'entity:7']), GRAPH_WAIT)
    const cy = live()[0]
    expect(cy.elements('.derived').length).toBe(0)
    expect([cy.$id('relationship:1').length, cy.$id('relationship:2').length]).toEqual([0, 0])
  }, 20_000)

  it('XC-9: several genes on one entry each name their own release', async () => {
    serveNetworks({ '?seeds=entity:985&expand=reference': sharedEntryNetwork as unknown as JsonBodyType })
    renderGraphRoute('/explore?v=1&seeds=entity%3A985')
    await answered('/api/graph/v1/network?seeds=entity:985&expand=reference')
    await waitFor(() => expect(live()).toHaveLength(1), GRAPH_WAIT)
    const cy = live()[0]
    await waitFor(() => expect(cy.$id('entity:986').length).toBe(1), GRAPH_WAIT)
    expect(['entity:986', 'entity:987', 'entity:988', 'entity:989'].map((id) => cy.$id(id).data('label')))
      .toEqual(['SYN-N986\nvia Encodes', 'SYN-N987\nvia Encodes', 'SYN-N988\nvia Encodes', 'SYN-N985-1\nvia Isoform of'])
    for (const id of ['entity:986', 'entity:987', 'entity:988', 'entity:989']) expect(cy.$id(id).hasClass('derived'), id).toBe(true)
    expect(['relationship:984', 'relationship:985', 'relationship:986', 'relationship:987'].map((id) => cy.$id(id).data('label'))).toEqual([
      'Encodes · Synthetic extras mapping x1', 'Encodes · Synthetic extras mapping x1', 'Encodes · Synthetic extras mapping x1',
      'Isoform of · Synthetic extras isoforms x1',
    ])
  }, 20_000)
})
