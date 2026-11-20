import { act, cleanup, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import cytoscape from 'cytoscape'
import type { Core, LayoutOptions } from 'cytoscape'
import { http, HttpResponse } from 'msw'
import type { JsonBodyType } from 'msw'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import type { MockInstance } from 'vitest'
import {
  hostile, hostileTypedInstance, makeBrowserResponse, makeInstance, presentationAllKeys, twoReleaseInstance,
} from '../../mocks/fixtures/typedGraph/extras'
import { typedGraphOffHandlers } from '../../mocks/handlers/typedGraph'
import { server } from '../../mocks/server'
import { useAuthStore } from '../../store/authStore'
import { GRAPH_WAIT, recordRequests, recorded } from '../../test/graphHooks'
import type { RequestLog } from '../../test/graphHooks'
import { recordAnswers, renderGraphRoute } from '../../test/graphRoutes'
import type { GraphBrowserResponse, GraphInstance, GraphNetworkResponse } from '../graph/types'
import { isolateText } from '../graph/words/isolateText'

const log = vi.hoisted(() => ({ instances: [], runs: new Map() }))
vi.mock('react-cytoscapejs', async (importOriginal) =>
  (await import('../../test/headlessCytoscape')).headlessCytoscapeModule(importOriginal, log))

let requests: RequestLog
let answers: { answers: string[]; stop: () => void }
let scrolls: MockInstance

// The first import of a page transforms its modules, which can take longer than a row's waits on a loaded host.
beforeAll(async () => {
  await import('./GalleryPage')
  await import('./InstancePage')
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

// Words copied from the spec's sections 3.1 and 3.5 to 3.7, never from the modules under test.
const NOT_ENABLED = 'Browsers are not enabled on this portal.'
const TRY_AGAIN = 'Try again'
const HEADINGS = { releases: 'Data releases' }
const PRIMARY = "the study's own release"
const RELEASES_IN_NETWORK = 'Releases in this network'
const TABLE_REGION_LABEL = 'Table of relationships in view'
const SEED_LABEL = 'Name or identifier'
const NETWORK_HEADING = 'Network'
const removeText = (name: string) => `Remove ${isolateText(name)}`
// F2's and F3's words, as the rows give them: the table's caption, the legend's title, the canvas line, the release terms
// and lines, and the entity and row names, which take no isolates.
const CAPTION = 'Relationships in view'
const LEGEND = 'Legend'
const LOADING_NETWORK = 'Loading the network…'
const LICENCE = 'Licence'
const RELEASE = 'Release'
const ATTRIBUTION = 'Attribution'
const CHANGES = "What openPIP's import changes"
const NO_LICENCE = 'no name or SPDX identifier recorded'
const NO_ATTRIBUTION = 'none given'
const NO_ADAPTER = 'not available: the release names no import adapter that this portal has'
const CLOSE = 'Close'
const TF1 = 'SYN-TF1 (Gene)'
const TF1_G1 = 'Transcriptional regulation: SYN-TF1 (Gene) to SYN-G1 (Gene)'
const P2_SHARED = 'Physical interaction between SYN-P2 (Protein (UniProtKB entry)) and SYN-SHARED (Protein (UniProtKB entry))'
const P2_P3 = 'Physical interaction between SYN-P2 (Protein (UniProtKB entry)) and SYN-P3 (Protein (UniProtKB entry))'
// The recorded titles and licence.
const TYPED_TITLE = 'Synthetic regulation example'
const OTHER_TITLE = 'SYN other browser'
const MIT = 'https://github.com/Helmy-Lab/openpip-2.0/blob/main/LICENSE'
const MIT_NAME = 'MIT License'

const PATH = '/browsers/synthetic-regulation?seeds=entity%3A7'
const STATUS = '/api/graph/v1/status'
const INSTANCE = '/api/graph/v1/browsers/synthetic-regulation'
const NETWORK_TF1 = '/api/graph/v1/network?seeds=entity:7&browser=synthetic-regulation'
const NETWORK_P3 = '/api/graph/v1/network?seeds=entity:13&browser=synthetic-regulation'
const RELATIONSHIP_6 = '/api/graph/v1/relationships/relationship%3A6?browser=synthetic-regulation'
const EVIDENCE_6 = '/api/graph/v1/relationships/relationship%3A6/evidence?browser=synthetic-regulation'
const asked = () => requests.urls.map((url) => url.pathname + url.search)
const page = () => document.querySelector('main') as HTMLElement
/** 12.0's address check: every address inside the page is http(s), the scheme in any case, or a single-slash router path. */
const strayAddresses = () => Array.from(page().querySelectorAll('a[href]'), (link) => link.getAttribute('href') ?? '')
  .filter((href) => !/^(?:https?:\/\/|\/(?!\/))/i.test(href))
const section = () => screen.getByRole('region', { name: NETWORK_HEADING })
const table = () => within(section()).getByRole('table', { name: CAPTION })
const rowButtons = () => Array.from(table().querySelectorAll('tbody th button'), (node) => node as HTMLButtonElement)
const rowNames = () => rowButtons().map((node) => node.textContent)
/** The Cytoscape instances still alive; a row taps only the one live instance. */
const live = () => (log.instances as Core[]).filter((cy) => !cy.destroyed())
const rowsShown = () => waitFor(() => expect(rowNames()).toContain(TF1_G1), GRAPH_WAIT)
/** One stub per slug, so that a recorded slug still reaches its recording. */
const serve = (instance: GraphInstance) =>
  server.use(http.get(`/api/graph/v1/browsers/${instance.slug}`, () => HttpResponse.json(makeBrowserResponse(instance))))
/** A network for another instance, answered only for a request under that instance's `browser`. */
function serveNetwork(slug: string, body: GraphNetworkResponse) {
  server.use(http.get('/api/graph/v1/network', ({ request }) =>
    (new URL(request.url).searchParams.get('browser') === slug ? HttpResponse.json(body) : undefined)))
}
const networkFor = (name: string, slug: string, change: Partial<GraphNetworkResponse> = {}): GraphNetworkResponse => {
  const body = recorded<GraphNetworkResponse>(name).body
  return { ...body, ...change, query: { ...body.query, browser: slug } }
}
/** A release list's items, each as its terms and their lines. */
const releaseItems = (list: Element) => within(list as HTMLElement).getAllByRole('listitem').map((item) => {
  const terms = Array.from(item.querySelectorAll('dt'), (node) => node.textContent ?? '')
  const lines = Array.from(item.querySelectorAll('dd'), (node) => node.textContent ?? '')
  return Object.fromEntries(terms.map((term, index) => [term, lines[index]]))
})
const networkReleases = () => within(section()).getByRole('heading', { level: 3, name: RELEASES_IN_NETWORK }).nextElementSibling as HTMLElement
/** F2's technique (NET/TypedNetwork.test.tsx:97-109): the name of each layout run, the constructor's 'null' one left out. */
function recordLayouts(): string[] {
  const names: string[] = []
  const probe = cytoscape({ headless: true })
  const core = Object.getPrototypeOf(probe) as Core
  probe.destroy()
  const original = core.layout
  vi.spyOn(core, 'layout').mockImplementation(function (this: Core, options: LayoutOptions) {
    if (options.name !== 'null') names.push(options.name)
    return original.call(this, options)
  })
  return names
}

describe('the instance workspace', () => {
  it('IC-1: the notice is mounted empty before the first answer, then three rows and one canvas, all under browser=', async () => {
    let release: () => void = () => {}
    const held = new Promise<void>((resolve) => {
      release = resolve
    })
    server.use(http.get('/api/graph/v1/network', async () => {
      await held
      return HttpResponse.json(recorded<JsonBodyType>('network-tf1-browser').body)
    }))
    renderGraphRoute(PATH)
    const region = await screen.findByRole('region', { name: NETWORK_HEADING }, GRAPH_WAIT)
    // F2's notice has no name: it is the section's one polite status region, beside F2's unnamed canvas line.
    const notices = region.querySelectorAll('[role="status"][aria-live="polite"]')
    expect(notices).toHaveLength(1)
    const notice = notices[0]
    expect(notice.textContent).toBe('')
    await waitFor(() => expect(asked()).toContain(NETWORK_TF1), GRAPH_WAIT)
    const canvasLine = notice.nextElementSibling
    expect(canvasLine?.getAttribute('role')).toBe('status')
    expect(canvasLine?.textContent).toBe(LOADING_NETWORK)
    release()
    await rowsShown()
    expect(rowNames()).toHaveLength(3)
    await waitFor(() => expect(live()).toHaveLength(1), GRAPH_WAIT)
    expect(log.instances).toHaveLength(1)
    expect(asked()).toEqual([STATUS, INSTANCE, NETWORK_TF1])
  }, 20_000)

  it('IC-2: a row opens the relationship panel at h3 under browser=, in the panel column, and Close returns focus to the row', async () => {
    const user = userEvent.setup()
    renderGraphRoute(PATH)
    await rowsShown()
    const row = within(table()).getByRole('button', { name: TF1_G1 })
    act(() => row.focus())
    await user.keyboard('{Enter}')
    const panel = await screen.findByRole('region', { name: TF1_G1 }, GRAPH_WAIT)
    expect(panel.querySelector('h1, h2, h3, h4, h5, h6')?.tagName).toBe('H3')
    // The panel's two requests follow F3's delay.
    await waitFor(() => expect(asked()).toHaveLength(5), GRAPH_WAIT)
    expect(asked()).toEqual([STATUS, INSTANCE, NETWORK_TF1, RELATIONSHIP_6, EVIDENCE_6])
    expect((panel.parentElement as HTMLElement).style.flex).toBe('1 1 320px')
    // The panel column follows the canvas column (7.3), so a wrapped panel stays beside the canvas.
    expect((panel.parentElement as HTMLElement).previousElementSibling).toBe(section().querySelector('[role="status"][aria-live="polite"]')?.parentElement)
    await waitFor(() => expect(answers.answers).toHaveLength(5), GRAPH_WAIT)
    await user.click(within(panel).getByRole('button', { name: CLOSE }))
    await waitFor(() => expect(screen.queryByRole('region', { name: TF1_G1 })).toBeNull(), GRAPH_WAIT)
    expect(document.activeElement).toBe(within(table()).getByRole('button', { name: TF1_G1 }))
  }, 20_000)

  it('IC-3: a canvas tap opens the entity panel under browser=, and Escape closes it', async () => {
    const user = userEvent.setup()
    renderGraphRoute(PATH)
    await rowsShown()
    await waitFor(() => expect(live()).toHaveLength(1), GRAPH_WAIT)
    const before = asked().length
    act(() => {
      live()[0].$id('entity:7').emit('tap')
    })
    const panel = await screen.findByRole('region', { name: TF1 }, GRAPH_WAIT)
    expect(panel.querySelector('h1, h2, h3, h4, h5, h6')?.tagName).toBe('H3')
    const entity = '/api/graph/v1/entities/entity%3A7?browser=synthetic-regulation'
    await waitFor(() => expect(asked().slice(before)).toEqual([entity]), GRAPH_WAIT)
    await waitFor(() => expect(answers.answers).toContain(`200 ${entity}`), GRAPH_WAIT)
    await user.keyboard('{Escape}')
    await waitFor(() => expect(screen.queryByRole('region', { name: TF1 })).toBeNull(), GRAPH_WAIT)
    expect(asked()).toEqual([STATUS, INSTANCE, NETWORK_TF1, entity])
  }, 20_000)

  it("IC-4: the header lists the version and licence, the network's list holds exactly its one release, and hostile release text stays text", async () => {
    // (a) The demonstration, restricted to one release.
    let view = renderGraphRoute(PATH)
    await rowsShown()
    const header = page().querySelector('[data-selectable]') as HTMLElement
    const versions = within(screen.getByRole('heading', { level: 2, name: HEADINGS.releases }).nextElementSibling as HTMLElement).getAllByRole('listitem')
    expect(versions).toHaveLength(1)
    expect(versions[0].querySelector('p')?.textContent).toBe(`${isolateText('Synthetic regulation example v2 (synthetic)')}: ${PRIMARY}`)
    expect(Array.from(versions[0].querySelectorAll('dt'), (node) => node.textContent)).toEqual([LICENCE])
    expect(versions[0].querySelector('dd')?.textContent).toBe(`${MIT_NAME} ${MIT}`)
    expect(Array.from(header.querySelectorAll('dt'), (node) => node.textContent)).not.toContain(RELEASE)
    const items = releaseItems(networkReleases())
    expect(items).toHaveLength(1)
    expect(items[0][RELEASE]).toBe('Synthetic regulation example v2')
    expect(section().textContent ?? '').not.toContain('v1')
    expect(items[0][LICENCE]).toBe(`${MIT_NAME} ${MIT}`)
    expect(items[0][ATTRIBUTION]).toBe('Copyright (c) 2016-2026 The openPIP authors')
    expect(items[0][CHANGES]).toBe(recorded<GraphBrowserResponse>('browser-typed').body.releases[0].changes_note)
    // The header's version line and the network's list each link the licence.
    const licences = Array.from(page().querySelectorAll('a')).filter((link) => link.getAttribute('href') === MIT)
    expect(licences).toHaveLength(2)
    for (const link of licences) expect(link.getAttribute('rel')).toBe('noopener noreferrer')
    expect(strayAddresses()).toEqual([])
    expect(asked()).toEqual([STATUS, INSTANCE, NETWORK_TF1])
    view.unmount()
    // (b) Two releases, listed in the network's order.
    serve(twoReleaseInstance)
    serveNetwork(twoReleaseInstance.slug, networkFor('network-tf1-browser', twoReleaseInstance.slug, { releases: makeBrowserResponse(twoReleaseInstance).releases }))
    let before = asked().length
    view = renderGraphRoute('/browsers/syn-two?seeds=entity%3A7')
    await rowsShown()
    const two = releaseItems(networkReleases())
    expect(two.map((item) => item[RELEASE])).toEqual(['Test labelled release x1', 'Synthetic extras x0'])
    for (const item of two) expect([item[LICENCE], item[ATTRIBUTION], item[CHANGES]]).toEqual([NO_LICENCE, NO_ATTRIBUTION, NO_ADAPTER])
    expect(asked().slice(before)).toEqual([STATUS, '/api/graph/v1/browsers/syn-two', '/api/graph/v1/network?seeds=entity:7&browser=syn-two'])
    view.unmount()
    // (c) F3's hostile release.
    server.resetHandlers()
    serve(hostileTypedInstance)
    serveNetwork(hostileTypedInstance.slug, networkFor('network-tf1-browser', hostileTypedInstance.slug, {
      releases: makeBrowserResponse(hostileTypedInstance).releases,
    }))
    before = asked().length
    view = renderGraphRoute('/browsers/syn-hostile-typed?seeds=entity%3A7')
    await rowsShown()
    const hostileItems = releaseItems(networkReleases())
    expect(hostileItems).toHaveLength(1)
    expect(hostileItems[0][RELEASE]).toBe(`${hostile('dataset.title')} ${hostile('version')}`)
    expect(hostileItems[0][LICENCE]).toBe(`${hostile('licence.name')} (${hostile('licence.spdx_id')}) javascript:alert(1)`)
    expect(hostileItems[0][LICENCE].endsWith('javascript:alert(1)')).toBe(true)
    expect(hostileItems[0][ATTRIBUTION]).toBe(hostile('attribution'))
    expect(hostileItems[0][CHANGES]).toBe(hostile('changes_note'))
    expect(networkReleases().querySelector('a')).toBeNull()
    expect(view.container.querySelector('[data-hostile]')).toBeNull()
    expect(strayAddresses()).toEqual([])
    expect(asked().slice(before)).toEqual([
      STATUS, '/api/graph/v1/browsers/syn-hostile-typed', '/api/graph/v1/network?seeds=entity:7&browser=syn-hostile-typed',
    ])
  }, 20_000)

  it('IC-5: the layout values: a wrapping row, the main column, the named scrolling table region, one legend, nothing hidden', async () => {
    renderGraphRoute(PATH)
    await rowsShown()
    const region = section()
    const notice = region.querySelector('[role="status"][aria-live="polite"]') as HTMLElement
    const main = notice.parentElement as HTMLElement
    const row = main.parentElement as HTMLElement
    expect(row.style.flexWrap).toBe('wrap')
    expect(main.style.flex).toBe('999 1 560px')
    expect(main.style.minWidth).toBe('0px')
    expect(main.querySelector('[role="img"]')).not.toBeNull()
    const tableRegion = within(region).getByRole('region', { name: TABLE_REGION_LABEL })
    expect(tableRegion.tabIndex).toBe(0)
    expect(tableRegion.style.overflowX).toBe('auto')
    expect(tableRegion.contains(table())).toBe(true)
    expect(tableRegion.querySelector('caption')?.textContent).toBe(CAPTION)
    expect(screen.getAllByText(LEGEND)).toHaveLength(1)
    // In 7.3's order (J28): the row, the table, the legend, then the network's releases.
    const order = [row, tableRegion, screen.getByText(LEGEND), within(region).getByRole('heading', { level: 3, name: RELEASES_IN_NETWORK })]
    order.slice(1).forEach((node, at) => expect(order[at].compareDocumentPosition(node) & Node.DOCUMENT_POSITION_FOLLOWING, `${at + 1}`).toBeTruthy())
    expect(within(region).queryByRole('button', { name: TRY_AGAIN })).toBeNull()
    expect(Array.from(region.querySelectorAll('div')).filter((node) => node.style.flex === '1 1 320px')).toEqual([])
    expect(document.querySelectorAll('[hidden]')).toHaveLength(0)
    expect(asked()).toEqual([STATUS, INSTANCE, NETWORK_TF1])
  }, 20_000)

  it("IC-6: the presentation's defaults reach the network and the panel requests, and the panel is named as its row", async () => {
    const user = userEvent.setup()
    const body = recorded<GraphBrowserResponse>('browser-typed').body
    server.use(http.get(INSTANCE, () => HttpResponse.json({ ...body, instance: { ...body.instance, presentation: presentationAllKeys } })))
    const network = '/api/graph/v1/network?seeds=entity:7&layers=transcriptional_regulation&direction=both&outcome=supported&browser=synthetic-regulation'
    renderGraphRoute(PATH)
    await waitFor(() => expect(asked()).toContain(network), GRAPH_WAIT)
    await rowsShown()
    expect(asked()).toEqual([STATUS, INSTANCE, network])
    // The row opened is relationship:6's.
    const row = within(table()).getByRole('button', { name: TF1_G1 })
    const before = asked().length
    act(() => row.focus())
    await user.keyboard('{Enter}')
    const panel = await screen.findByRole('region', { name: TF1_G1 }, GRAPH_WAIT)
    expect(panel.querySelector('h1, h2, h3, h4, h5, h6')?.textContent).toBe(TF1_G1)
    await waitFor(() => expect(asked().slice(before)).toEqual([
      '/api/graph/v1/relationships/relationship%3A6?outcome=supported&browser=synthetic-regulation',
      EVIDENCE_6,
    ]), GRAPH_WAIT)
    await waitFor(() => expect(answers.answers.slice(before)).toHaveLength(2), GRAPH_WAIT)
  }, 20_000)

  it('IC-7: removing the last seed unmounts the network, destroys the canvas and moves focus to the search box', async () => {
    const user = userEvent.setup()
    renderGraphRoute(PATH)
    await rowsShown()
    await waitFor(() => expect(live()).toHaveLength(1), GRAPH_WAIT)
    // The one live instance, taken before the click, so the check below reads this row's own canvas.
    const [cy] = live()
    expect(cy.destroyed()).toBe(false)
    await user.click(screen.getByRole('button', { name: removeText(TF1) }))
    await waitFor(() => expect(screen.queryByRole('region', { name: NETWORK_HEADING })).toBeNull(), GRAPH_WAIT)
    expect(cy.destroyed()).toBe(true)
    expect(live()).toEqual([])
    expect(document.activeElement).toBe(screen.getByRole('searchbox', { name: SEED_LABEL }))
    expect(asked()).toEqual([STATUS, INSTANCE, NETWORK_TF1])
  }, 20_000)

  it('IC-8: a seed change answered by a switched-off portal rechecks status once, and the gate then says not enabled', async () => {
    const { router } = renderGraphRoute(PATH)
    await rowsShown()
    server.use(...typedGraphOffHandlers)
    const before = asked().length
    const network = '/api/graph/v1/network?seeds=entity:13&browser=synthetic-regulation'
    await act(() => router.navigate('/browsers/synthetic-regulation?seeds=entity%3A13'))
    // The request follows F2's debounce.
    await waitFor(() => expect(asked().slice(before)).toContain(STATUS), GRAPH_WAIT)
    expect(await screen.findByText(NOT_ENABLED, {}, GRAPH_WAIT)).toBeInTheDocument()
    expect(answers.answers).toContain(`404 ${network}`)
    expect(asked().slice(before)).toEqual([network, STATUS])
  }, 20_000)

  it('IC-9: nothing crosses between two instances; Back reads the first from the cache with no focus or scroll', async () => {
    const user = userEvent.setup()
    const other = makeInstance({ slug: 'syn-other', title: OTHER_TITLE })
    serveNetwork(other.slug, networkFor('network-p3-browser', other.slug))
    server.use(http.get('/api/graph/v1/browsers/syn-other', () => HttpResponse.json(makeBrowserResponse(other))))
    const { router } = renderGraphRoute(PATH)
    await rowsShown()
    const row = within(table()).getByRole('button', { name: TF1_G1 })
    act(() => row.focus())
    await user.keyboard('{Enter}')
    await screen.findByRole('region', { name: TF1_G1 }, GRAPH_WAIT)
    await waitFor(() => expect(answers.answers).toHaveLength(5), GRAPH_WAIT)
    expect(asked()).toEqual([STATUS, INSTANCE, NETWORK_TF1, RELATIONSHIP_6, EVIDENCE_6])
    const before = asked().length
    await act(() => router.navigate('/browsers/syn-other?seeds=entity%3A13'))
    const heading = await screen.findByRole('heading', { level: 1, name: OTHER_TITLE }, GRAPH_WAIT)
    await waitFor(() => expect(rowNames()).toEqual([P2_SHARED]), GRAPH_WAIT)
    expect(screen.queryByRole('region', { name: TF1_G1 })).toBeNull()
    expect(asked().slice(before)).toEqual(['/api/graph/v1/browsers/syn-other', '/api/graph/v1/network?seeds=entity:13&browser=syn-other'])
    await waitFor(() => expect(live()).toHaveLength(1), GRAPH_WAIT)
    await waitFor(() => expect(document.activeElement).toBe(heading), GRAPH_WAIT)
    expect(scrolls).toHaveBeenCalledTimes(1)
    expect(scrolls).toHaveBeenCalledWith(0, 0)
    const back = asked().length
    await act(() => router.navigate(-1))
    expect(await screen.findByRole('heading', { level: 1, name: TYPED_TITLE }, GRAPH_WAIT)).toBeInTheDocument()
    await rowsShown()
    // A pause after which nothing more is expected: the first instance comes from the cache.
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 500))
    })
    expect(asked().slice(back)).toEqual([])
    expect(screen.queryByRole('region', { name: TF1_G1 })).toBeNull()
    expect(document.activeElement).toBe(document.body)
    expect(scrolls).toHaveBeenCalledTimes(1)
  }, 20_000)

  it('IC-10: the presentation reaches the canvas and the table: the namespace names entity:13 by its identifier, and the canvas runs the layout it names', async () => {
    const layouts = recordLayouts()
    const body = recorded<GraphBrowserResponse>('browser-typed').body
    const presentation = { node_label_namespace: 'synthetic_protein', layout: 'grid' }
    server.use(http.get(INSTANCE, () => HttpResponse.json({ ...body, instance: { ...body.instance, presentation } })))
    renderGraphRoute('/browsers/synthetic-regulation?seeds=entity%3A13')
    await waitFor(() => expect(rowNames()).toEqual([P2_P3]), GRAPH_WAIT)
    await waitFor(() => expect(live()).toHaveLength(1), GRAPH_WAIT)
    expect(live()[0].$id('entity:13').data('label')).toBe('SYN-P3')
    await waitFor(() => expect(layouts).toContain('grid'), GRAPH_WAIT)
    expect(layouts).not.toContain('cose')
    expect(asked()).toEqual([STATUS, INSTANCE, NETWORK_P3])
  }, 20_000)
})
