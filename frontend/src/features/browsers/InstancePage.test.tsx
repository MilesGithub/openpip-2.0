import { onlineManager } from '@tanstack/react-query'
import { act, cleanup, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import type { JsonBodyType } from 'msw'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  citationCardInstance, crossTaxonInstance, externalInstance, hostile, hostileInstance, hostileTypedInstance, makeBrowserResponse, makeInstance,
  papersInstance, plainInstance, plannedInstance, plannedPortalInstance, primaryOutsideInstance, retiredInstance, retiredPortalInstance,
  stagedInstance, syntheticPaperInstance, twoReleaseInstance, unavailableInstance, unknownTaxonInstance,
} from '../../mocks/fixtures/typedGraph/extras'
import { typedGraphOffHandlers, typedGraphVisitorHandlers } from '../../mocks/handlers/typedGraph'
import { server } from '../../mocks/server'
import { useAuthStore } from '../../store/authStore'
import { GRAPH_WAIT, recordRequests, recorded } from '../../test/graphHooks'
import type { RequestLog } from '../../test/graphHooks'
import { recordAnswers, renderGraphRoute } from '../../test/graphRoutes'
import type { GraphBrowserResponse, GraphInstance, GraphResolveResponse, GraphStatus } from '../graph/types'
import { isolateText } from '../graph/words/isolateText'

// The network's request still goes out through the workspace's hook; only the drawing is left out.
vi.mock('../graph/network/TypedNetwork', () => ({ TypedNetwork: () => null }))

let requests: RequestLog
let answers: { answers: string[]; stop: () => void }

// The first import of a page transforms its modules, which can take longer than a row's waits on a loaded host.
beforeAll(async () => {
  await import('./GalleryPage')
  await import('./InstancePage')
}, 60_000)

beforeEach(() => {
  useAuthStore.getState().logout()
  requests = recordRequests()
  answers = recordAnswers()
  vi.spyOn(console, 'warn')
  vi.spyOn(console, 'error')
  // jsdom prints "Not implemented" for a call no spy replaces, which no console spy sees; no row here reads the calls.
  vi.spyOn(window, 'scrollTo').mockImplementation(() => {})
})

afterEach(() => {
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

// Words copied from the spec's sections 3.1 and 3.3 to 3.7, with 6.1's title words and 6.2's taxon words, never from the modules under test.
const TITLE = 'Browsers'
const NOT_ENABLED = 'Browsers are not enabled on this portal.'
const TRY_AGAIN = 'Try again'
const SERVER_UNREACHABLE = 'The server could not be reached.'
const THROTTLED_SEVEN = 'Too many requests. Try again in 7 seconds.'
const TYPED_LIVE = 'Typed data · Live'
const STAGED_STATE = 'Typed data · Data unavailable'
const PORTAL_STATE = "This portal's search"
const STAFF_ONLY = 'Staff only'
const UNLISTED = 'Unlisted'
const PRIMARY = "the study's own release"
const CONTEXT = 'a release that supports it'
const PORTAL_SEARCH_ACTION = "Open this portal's search"
const EXTERNAL_ACTION = 'External portal at'
const NOT_AVAILABLE = 'This browser is not available to you.'
const BROWSER_ERROR = 'This browser could not be loaded.'
const LOADING_BROWSER = 'Loading this browser…'
const BACK_LINK = 'Back to the gallery'
const HEADINGS = { description: 'Description', methods: 'Methods', citation: 'Citation', organisms: 'Organisms', releases: 'Data releases' }
const NO_RELEASE = 'No release of this browser is available to you.'
const SYNTHETIC_BADGE = 'Synthetic demonstration data'
const SYNTHETIC_EXPLAINED = 'Made up to demonstrate openPIP; it describes no real biology.'
const BADGE_LINE = `${SYNTHETIC_BADGE} ${SYNTHETIC_EXPLAINED}`
const SEED_SECTION_HEADING = 'Find entities in this browser'
const SEED_FORM_NAME = 'Search this browser'
const SEED_LABEL = 'Name or identifier'
const SEARCHING = 'Searching…'
const ONE_TERM = 'Search for one name or identifier at a time. A name that holds a comma or tab cannot be searched: use an identifier.'
const SEARCH_FAILED_AGAIN = 'The search could not be run. Press Search to try again.'
const NO_SEED_PROMPT = 'Search for an entity to draw its network in this browser.'
const SEEDS_ADDRESS_ERROR = 'This page cannot use the entities named in this address.'
const SEEDS_REMOVAL = 'Show this browser with no entities chosen'
const NETWORK_HEADING = 'Network'
const CANDIDATES_NAME = 'Matching entities'
const CHOSEN_LABEL = 'Chosen entities'
const REMOVE_ALL = 'Remove all chosen entities'
const CHOSEN_MARK = ' (chosen)'
const LIMIT_ONE = 'The network takes at most 1 entity. Remove one to add another.'
const NO_TITLE = 'title not recorded'
const SYNTHETIC_SUFFIX = ' (synthetic)'
const SYNTHETIC_TAXON = 'synthetic construct (taxid 32630)'
const SYNTHETIC_ORGANISM = 'synthetic construct'
const chosenText = (name: string) => `${isolateText(name)} is among the chosen entities.`
const notChosenText = (name: string) => `${isolateText(name)} is not among the chosen entities. Press Search to add it.`
const candidateText = (term: string) =>
  `“${isolateText(term)}” matches one entity, though not as its primary identifier is written. Add it if it is the one you meant.`
const ambiguousText = (term: string) => `“${isolateText(term)}” matches more than one entity. Add the one you meant.`
const unmatchedText = (term: string, organism: string | null) => (organism === null
  ? `No entity in this browser matches “${isolateText(term)}”.`
  : `No ${isolateText(organism)} entity in this browser matches “${isolateText(term)}”.`)
const addText = (name: string) => `Add ${isolateText(name)}`
const removeText = (name: string) => `Remove ${isolateText(name)}`
// F3's words, as the rows give them: the release terms and lines, the entity names and a table row's name, none isolated.
const LICENCE = 'Licence'
const NO_LICENCE = 'no name or SPDX identifier recorded'
const FULL_LIST_TERMS = ['Release', 'Attribution', "What openPIP's import changes"]
const FULL_LIST_LINES = ['Attribution', 'none given', "What openPIP's import changes", 'not available: the release names no import adapter that this portal has']
const TF1 = 'SYN-TF1 (Gene)'
const SHARED_GENE = 'SYN-SHARED (Gene)'
const SHARED_PROTEIN = 'SYN-SHARED (Protein (UniProtKB entry))'
const TF1_G1 = 'Transcriptional regulation: SYN-TF1 (Gene) to SYN-G1 (Gene)'
// The recorded titles and licence.
const TYPED_TITLE = 'Synthetic regulation example'
const PORTAL_TITLE = 'Protein interactions on this portal'
const MIT = 'https://github.com/Helmy-Lab/openpip-2.0/blob/main/LICENSE'
const MIT_NAME = 'MIT License'

const STATUS = '/api/graph/v1/status'
const INSTANCE = '/api/graph/v1/browsers/synthetic-regulation'
const RESOLVE_TF1 = '/api/graph/v1/resolve?q=SYN-TF1&taxon=32630&browser=synthetic-regulation'
const RESOLVE_CASE = '/api/graph/v1/resolve?q=syn-tf1&taxon=32630&browser=synthetic-regulation'
const RESOLVE_SHARED = '/api/graph/v1/resolve?q=SYN-SHARED&taxon=32630&browser=synthetic-regulation'
const RESOLVE_NOPE = '/api/graph/v1/resolve?q=SYN-NOPE&taxon=32630&browser=synthetic-regulation'
const NETWORK_TF1 = '/api/graph/v1/network?seeds=entity:7&browser=synthetic-regulation'
const NETWORK_SHARED = '/api/graph/v1/network?seeds=entity:13&browser=synthetic-regulation'
const NETWORK_BOTH = '/api/graph/v1/network?seeds=entity:13,entity:7&browser=synthetic-regulation'
const instancePath = (instance: GraphInstance) => `/api/graph/v1/browsers/${instance.slug}`
const asked = () => requests.urls.map((url) => url.pathname + url.search)
const page = () => document.querySelector('main') as HTMLElement
/** 12.0's address check: every address inside the page is http(s), the scheme in any case, or a single-slash router path. */
const strayAddresses = () => Array.from(page().querySelectorAll('a[href]'), (link) => link.getAttribute('href') ?? '')
  .filter((href) => !/^(?:https?:\/\/|\/(?!\/))/i.test(href))
/** One stub per slug, so that a recorded slug still reaches its recording. */
const serve = (instance: GraphInstance) =>
  server.use(http.get(instancePath(instance), () => HttpResponse.json(makeBrowserResponse(instance))))
const statusWith = (change: (body: GraphStatus) => JsonBodyType) =>
  server.use(http.get(STATUS, () => HttpResponse.json(change(recorded<GraphStatus>('status-on').body))))
const title = (name: string) => screen.findByRole('heading', { level: 1, name }, GRAPH_WAIT)
const header = () => page().querySelector('[data-selectable]') as HTMLElement
const seedSection = () => screen.getByRole('region', { name: SEED_SECTION_HEADING })
const seedLine = () => within(seedSection()).getByRole('status')
const seedBox = () => screen.getByRole('searchbox', { name: SEED_LABEL })
const seeds = (router: { state: { location: { search: string } } }) => new URLSearchParams(router.state.location.search).get('seeds')
const chosenItems = () => within(screen.getByRole('list', { name: CHOSEN_LABEL })).getAllByRole('listitem')
const candidateButtons = () => within(screen.getByRole('list', { name: CANDIDATES_NAME })).getAllByRole('button')
const headingList = (name: string) => screen.getByRole('heading', { level: 2, name }).nextElementSibling as HTMLElement
const lineIs = (text: string) => waitFor(() => expect(seedLine().textContent).toBe(text), GRAPH_WAIT)
/** The page's own status line, the first inside `main`, above the header. */
const routeLine = () => page().querySelector('[role="status"]') as HTMLElement

function held() {
  let release: () => void = () => {}
  const promise = new Promise<void>((resolve) => {
    release = resolve
  })
  return { promise, release: () => release() }
}

/**
 * Every text the element takes while watched: the value each mutation replaced, read from the record when it is made, and the
 * one the element holds at each callback, so two changes delivered in one callback are both seen.
 */
function watchTexts(element: HTMLElement) {
  const texts: string[] = []
  const take = (records: MutationRecord[]) => {
    for (const record of records) {
      if (record.type === 'characterData' && record.oldValue !== null) texts.push(record.oldValue)
      for (const node of Array.from(record.removedNodes)) texts.push(node.textContent ?? '')
    }
    texts.push(element.textContent ?? '')
  }
  const observer = new MutationObserver(take)
  observer.observe(element, { childList: true, characterData: true, characterDataOldValue: true, subtree: true })
  return {
    texts,
    stop: () => {
      take(observer.takeRecords())
      observer.disconnect()
    },
  }
}

describe('the instance page', () => {
  it('IP-1: the typed header in order, its version and licence lines, the way back, the seed search and no network', async () => {
    renderGraphRoute('/browsers/synthetic-regulation')
    expect(await title(TYPED_TITLE)).toBeInTheDocument()
    expect(screen.getAllByRole('heading', { level: 1 }).map((node) => node.textContent)).toEqual([TYPED_TITLE])
    const state = screen.getByText(TYPED_LIVE)
    // The badge, then the sentence, in one paragraph: the badge's words are read once.
    const badgeLines = Array.from(page().querySelectorAll('p')).filter((node) => node.textContent === BADGE_LINE)
    expect(badgeLines).toHaveLength(1)
    expect(badgeLines[0].firstElementChild?.textContent).toBe(SYNTHETIC_BADGE)
    expect((page().textContent ?? '').split(SYNTHETIC_BADGE)).toHaveLength(2)
    const visibility = screen.getByText(STAFF_ONLY)
    const summary = screen.getByText(recorded<GraphBrowserResponse>('browser-typed').body.instance.summary)
    const citations = within(headingList(HEADINGS.citation)).getAllByRole('listitem')
      .map((item) => Array.from(item.querySelectorAll('p'), (line) => line.textContent))
    expect(citations).toEqual([
      [`${isolateText('SYN demonstration paper 1 (synthetic: not a real publication)')}${SYNTHETIC_SUFFIX}`, '2026', 'local key syn-paper-1'],
      [`${isolateText('SYN demonstration paper 2 (synthetic: not a real publication)')}${SYNTHETIC_SUFFIX}`, '2026', 'local key syn-paper-2'],
    ])
    expect(within(headingList(HEADINGS.organisms)).getAllByRole('listitem').map((item) => item.textContent)).toEqual([SYNTHETIC_TAXON])
    const versions = within(headingList(HEADINGS.releases)).getAllByRole('listitem')
    expect(versions).toHaveLength(1)
    expect(versions[0].querySelector('p')?.textContent).toBe(`${isolateText('Synthetic regulation example v2 (synthetic)')}: ${PRIMARY}`)
    expect(versions[0].querySelector('p')?.nextElementSibling?.tagName).toBe('DL')
    expect(versions[0].querySelector('dt')?.textContent).toBe(LICENCE)
    expect(versions[0].querySelector('dd')?.textContent).toBe(`${MIT_NAME} ${MIT}`)
    const licence = within(versions[0]).getByRole('link', { name: MIT })
    expect(licence).toHaveAttribute('href', MIT)
    expect(licence).toHaveAttribute('target', '_blank')
    expect(licence.getAttribute('rel')).toBe('noopener noreferrer')
    const terms = Array.from(page().querySelectorAll('dt'), (node) => node.textContent)
    for (const term of FULL_LIST_TERMS) expect(terms).not.toContain(term)
    const back = screen.getByRole('link', { name: BACK_LINK })
    expect(back).toHaveAttribute('href', '/browsers')
    expect(page().querySelectorAll('[data-selectable]')).toHaveLength(1)
    for (const node of [screen.getByRole('heading', { level: 1 }), state, badgeLines[0], visibility, summary, headingList(HEADINGS.citation),
      headingList(HEADINGS.organisms), headingList(HEADINGS.releases), back]) {
      expect(header().contains(node)).toBe(true)
    }
    // In 10.4's order (J23): each header element follows the one before it.
    const order = [screen.getByRole('heading', { level: 1 }), state, badgeLines[0], visibility, summary,
      ...[HEADINGS.citation, HEADINGS.organisms, HEADINGS.releases].map((name) => screen.getByRole('heading', { level: 2, name })), back]
    order.slice(1).forEach((node, at) => expect(order[at].compareDocumentPosition(node) & Node.DOCUMENT_POSITION_FOLLOWING, `${at + 1}`).toBeTruthy())
    expect(screen.getByRole('heading', { level: 2, name: SEED_SECTION_HEADING })).toBeInTheDocument()
    expect(screen.getByRole('search', { name: SEED_FORM_NAME })).toBeInTheDocument()
    expect((seedBox() as HTMLInputElement).maxLength).toBe(200)
    expect(screen.getByText(NO_SEED_PROMPT)).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: NETWORK_HEADING })).toBeNull()
    expect(asked()).toEqual([STATUS, INSTANCE])
  }, 20_000)

  const OTHERS: [string, GraphInstance, string, boolean][] = [
    ['plannedInstance', plannedInstance, 'Typed data · Planned', true],
    ['retiredInstance', retiredInstance, 'Typed data · Retired', true],
    ['externalInstance', externalInstance, 'External portal', false],
    ['citationCardInstance', citationCardInstance, 'Citation card · No data', true],
    ['unavailableInstance', unavailableInstance, 'Typed data · Data unavailable', false],
    ['plannedPortalInstance', plannedPortalInstance, "This portal's search · Planned", false],
    ['retiredPortalInstance', retiredPortalInstance, "This portal's search · Retired", false],
    ['stagedInstance', stagedInstance, STAGED_STATE, true],
  ]
  it.each(OTHERS)('IP-2: %s shows its state and badge, no seed search and no network, with seeds in the address', async (_name, instance, state, badge) => {
    serve(instance)
    const { router } = renderGraphRoute(`/browsers/${instance.slug}?seeds=entity%3A7`)
    expect(await title(instance.title)).toBeInTheDocument()
    expect(within(header()).getByText(state)).toBeInTheDocument()
    expect(within(header()).queryByText(SYNTHETIC_BADGE) !== null).toBe(badge)
    expect(within(header()).queryByText(STAFF_ONLY) !== null).toBe(instance === stagedInstance)
    expect(within(header()).queryByText(UNLISTED)).toBeNull()
    expect(screen.queryByRole('search', { name: SEED_FORM_NAME })).toBeNull()
    expect(screen.queryByRole('heading', { name: SEED_SECTION_HEADING })).toBeNull()
    expect(screen.queryByRole('heading', { name: NETWORK_HEADING })).toBeNull()
    expect(asked()).toEqual([STATUS, instancePath(instance)])
    expect(router.state.location.pathname).toBe(`/browsers/${instance.slug}`)
    expect(router.state.location.search).toBe('?seeds=entity%3A7')
    if (instance === externalInstance) {
      const outbound = screen.getByRole('link', { name: 'https://portal.example/browse' })
      expect(outbound).toHaveAttribute('href', 'https://portal.example/browse')
      expect(outbound.getAttribute('rel')).toBe('noopener noreferrer')
      expect(outbound.parentElement?.textContent).toBe(`${EXTERNAL_ACTION} https://portal.example/browse`)
    }
    if (instance === plannedPortalInstance || instance === retiredPortalInstance) {
      expect(Array.from(page().querySelectorAll('a[href]'), (link) => link.getAttribute('href'))).not.toContain('/search')
    }
    if (instance === unavailableInstance) {
      expect(screen.getByText(NO_RELEASE)).toBeInTheDocument()
      // 10.4's noRelease branch: the sentence under its own 'Data releases' heading, inside the header.
      expect(within(header()).getByRole('heading', { level: 2, name: HEADINGS.releases }).nextElementSibling?.textContent).toBe(NO_RELEASE)
    }
  }, 20_000)

  it.each(['resolve', 'network'])('IP-2: with status saying %s is off, the typed header shows with no seed search and no network', async (key) => {
    // Every hand-written instance is served beside the recorded one, so a stub that answered every slug would show here.
    for (const [, instance] of OTHERS) serve(instance)
    statusWith((body) => ({ ...body, features: { ...body.features, [key]: false } }))
    renderGraphRoute('/browsers/synthetic-regulation?seeds=entity%3A7')
    expect(await title(TYPED_TITLE)).toBeInTheDocument()
    expect(screen.getAllByRole('heading', { level: 1 }).map((node) => node.textContent)).toEqual([TYPED_TITLE])
    expect(within(header()).getByText(TYPED_LIVE)).toBeInTheDocument()
    expect(screen.queryByRole('search', { name: SEED_FORM_NAME })).toBeNull()
    expect(screen.queryByRole('heading', { name: SEED_SECTION_HEADING })).toBeNull()
    expect(screen.queryByRole('heading', { name: NETWORK_HEADING })).toBeNull()
    expect(asked()).toEqual([STATUS, INSTANCE])
  }, 20_000)

  it("IP-3: the portal's instance shows its search link and no seed search, with no redirect", async () => {
    const { router } = renderGraphRoute('/browsers/protein-interactions')
    expect(await title(PORTAL_TITLE)).toBeInTheDocument()
    expect(screen.getByText(PORTAL_STATE)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: PORTAL_SEARCH_ACTION })).toHaveAttribute('href', '/search')
    expect(screen.queryByRole('search', { name: SEED_FORM_NAME })).toBeNull()
    expect(screen.queryByRole('heading', { name: HEADINGS.releases })).toBeNull()
    expect(router.state.location.pathname).toBe('/browsers/protein-interactions')
    expect(asked()).toEqual([STATUS, '/api/graph/v1/browsers/protein-interactions'])
  }, 20_000)

  it('IP-4: a visitor and an unknown slug read not available, with no Try again but the way back, and status is rechecked once', async () => {
    server.use(...typedGraphVisitorHandlers)
    const view = renderGraphRoute('/browsers/synthetic-regulation')
    expect(await screen.findByText(NOT_AVAILABLE, {}, GRAPH_WAIT)).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 1, name: TITLE })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: TRY_AGAIN })).toBeNull()
    expect(document.body.textContent ?? '').not.toContain('does not exist')
    expect(screen.getByRole('link', { name: BACK_LINK })).toHaveAttribute('href', '/browsers')
    // A pause after which nothing more is expected: the recheck must not repeat.
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 1000))
    })
    expect(asked()).toEqual([STATUS, INSTANCE, STATUS])
    view.unmount()
    server.resetHandlers()
    const before = asked().length
    renderGraphRoute('/browsers/no-such-instance', { state: { gallerySearch: '?tag=gene_regulation' } })
    expect(await screen.findByText(NOT_AVAILABLE, {}, GRAPH_WAIT)).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 1, name: TITLE })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: TRY_AGAIN })).toBeNull()
    expect(document.body.textContent ?? '').not.toContain('does not exist')
    // Reached from a filtered gallery, the way back keeps its search, as the header's does.
    expect(screen.getByRole('link', { name: BACK_LINK })).toHaveAttribute('href', '/browsers?tag=gene_regulation')
    await waitFor(() => expect(asked().slice(before)).toEqual([STATUS, '/api/graph/v1/browsers/no-such-instance', STATUS]), GRAPH_WAIT)
  }, 20_000)

  it('IP-5: failures offer Try again; a held retry stays off and focused, the title takes focus when it goes, and a held answer reads as loading', async () => {
    const user = userEvent.setup()
    // (a) A 429, then a retry whose answer is held until the row releases it.
    let calls = 0
    const second = held()
    server.use(http.get(INSTANCE, async () => {
      calls += 1
      if (calls === 1) return HttpResponse.json({ detail: 'Request was throttled.' }, { status: 429, headers: { 'Retry-After': '7' } })
      await second.promise
      return HttpResponse.json(recorded<JsonBodyType>('browser-typed').body)
    }))
    let view = renderGraphRoute('/browsers/synthetic-regulation')
    expect(await screen.findByText(THROTTLED_SEVEN, {}, GRAPH_WAIT)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: TRY_AGAIN }))
    await waitFor(() => expect(screen.getByRole('button', { name: TRY_AGAIN })).toHaveAttribute('aria-disabled', 'true'), GRAPH_WAIT)
    expect(document.activeElement).toBe(screen.getByRole('button', { name: TRY_AGAIN }))
    // Never `disabled` (4.7): jsdom keeps focus on a disabled button, where a browser takes it away.
    expect(screen.getByRole('button', { name: TRY_AGAIN })).toBeEnabled()
    second.release()
    const heading = await title(TYPED_TITLE)
    await waitFor(() => expect(document.activeElement).toBe(heading), GRAPH_WAIT)
    expect(asked()).toEqual([STATUS, INSTANCE, INSTANCE])
    view.unmount()
    // (b) to (d): a JSON 500, a 200 that is no instance body, and a network failure after F1's one retry.
    const failures: [() => Response, string, number][] = [
      [() => HttpResponse.json({ detail: 'x' }, { status: 500 }), BROWSER_ERROR, 1],
      [() => HttpResponse.json({ detail: 'x' }), BROWSER_ERROR, 1],
      [() => HttpResponse.error(), SERVER_UNREACHABLE, 2],
    ]
    for (const [reply, words, sent] of failures) {
      server.resetHandlers()
      server.use(http.get(INSTANCE, reply))
      const before = asked().length
      view = renderGraphRoute('/browsers/synthetic-regulation')
      expect(await screen.findByText(words, {}, GRAPH_WAIT)).toBeInTheDocument()
      expect(screen.getByRole('button', { name: TRY_AGAIN })).toBeInTheDocument()
      expect(asked().slice(before)).toEqual([STATUS, ...Array.from({ length: sent }, () => INSTANCE)])
      view.unmount()
    }
    // (e) The answer held: the page's own status line says the browser is loading until it comes.
    server.resetHandlers()
    const answer = held()
    server.use(http.get(INSTANCE, async () => {
      await answer.promise
      return HttpResponse.json(recorded<JsonBodyType>('browser-typed').body)
    }))
    const mark = asked().length
    renderGraphRoute('/browsers/synthetic-regulation')
    await waitFor(() => expect(asked().slice(mark)).toEqual([STATUS, INSTANCE]), GRAPH_WAIT)
    expect(routeLine().textContent).toBe(LOADING_BROWSER)
    answer.release()
    expect(await title(TYPED_TITLE)).toBeInTheDocument()
    expect(routeLine().textContent).toBe('')
  }, 20_000)

  it('IP-6: every hostile header field is literal text, no address but http(s) or a router path reaches href, and long text shows whole', async () => {
    serve(hostileInstance)
    let view = renderGraphRoute('/browsers/syn-hostile')
    expect(await title(hostile('title'))).toBeInTheDocument()
    expect(page().textContent ?? '').not.toContain(hostile('short_title'))
    expect(within(header()).getByText(hostile('summary'))).toBeInTheDocument()
    const description = headingList(HEADINGS.description)
    expect(description.textContent).toBe(`${hostile('description')}\nSecond line of the description.`)
    expect(headingList(HEADINGS.methods).textContent).toBe(hostile('methods_text'))
    // In 10.4's order (J23): the summary, then the description, the methods and the citation.
    const middle = [within(header()).getByText(hostile('summary')),
      ...[HEADINGS.description, HEADINGS.methods, HEADINGS.citation].map((name) => screen.getByRole('heading', { level: 2, name }))]
    middle.slice(1).forEach((node, at) => expect(middle[at].compareDocumentPosition(node) & Node.DOCUMENT_POSITION_FOLLOWING, `${at + 1}`).toBeTruthy())
    expect(header().textContent ?? '').not.toContain(hostile('tags[0]'))
    const paper = within(headingList(HEADINGS.citation)).getAllByRole('listitem')[0]
    expect(Array.from(paper.querySelectorAll('p'), (line) => line.textContent)).toEqual([
      `${isolateText(hostile('publications[0].title'))}${SYNTHETIC_SUFFIX}`,
      `${hostile('publications[0].authors')}, ${hostile('publications[0].journal')}`,
      `PMID ${hostile('pmid')}; DOI ${hostile('doi')}; PMCID ${hostile('pmcid')}; IMEx ${hostile('imex_id')}; local key ${hostile('local_key')}`,
      'data:text/html,<b data-hostile="url">x</b>',
    ])
    expect(paper.querySelector('a')).toBeNull()
    expect(screen.getByText(`${EXTERNAL_ACTION} javascript:alert(1)`).closest('a')).toBeNull()
    expect(view.container.querySelector('[data-hostile]')).toBeNull()
    expect(strayAddresses()).toEqual([])
    expect(header().style.whiteSpace).toBe('pre-wrap')
    expect(description.textContent).toContain('\n')
    expect(asked()).toEqual([STATUS, instancePath(hostileInstance)])
    view.unmount()
    server.resetHandlers()
    serve(hostileTypedInstance)
    let before = asked().length
    view = renderGraphRoute('/browsers/syn-hostile-typed')
    expect(await title(hostileTypedInstance.title)).toBeInTheDocument()
    const version = within(headingList(HEADINGS.releases)).getAllByRole('listitem')[0]
    expect(version.querySelector('p')?.textContent)
      .toBe(`${isolateText(`${hostile('dataset.title')} ${hostile('version')}${SYNTHETIC_SUFFIX}`)}: ${PRIMARY}`)
    expect(version.querySelector('dd')?.textContent).toBe(`${hostile('licence.name')} (${hostile('licence.spdx_id')}) javascript:alert(1)`)
    expect(version.querySelector('a')).toBeNull()
    const addresses = within(headingList(HEADINGS.citation)).getAllByRole('listitem').map((item) => {
      const lines = Array.from(item.querySelectorAll('p'), (line) => line.textContent)
      return [lines[lines.length - 1], item.querySelector('a')]
    })
    expect(addresses).toEqual([[' https://example.org/x', null], ['//example.org/x', null], ['ms-msdt:/id x', null]])
    expect(view.container.querySelector('[data-hostile]')).toBeNull()
    expect(strayAddresses()).toEqual([])
    expect(header().style.whiteSpace).toBe('pre-wrap')
    expect(asked().slice(before)).toEqual([STATUS, instancePath(hostileTypedInstance)])
    view.unmount()
    server.resetHandlers()
    const long = makeInstance({ description: 'SYN description text. '.repeat(400) })
    serve(long)
    before = asked().length
    renderGraphRoute(`/browsers/${long.slug}`)
    expect(await title(long.title)).toBeInTheDocument()
    expect(headingList(HEADINGS.description).textContent).toHaveLength(8800)
    expect(page().textContent ?? '').not.toContain('more characters not shown')
    expect(asked().slice(before)).toEqual([STATUS, instancePath(long)])
  }, 20_000)

  it('IP-7: each version line in selection order is followed by its licence line, and the full list is not in the header', async () => {
    serve(twoReleaseInstance)
    const view = renderGraphRoute('/browsers/syn-two')
    expect(await title(twoReleaseInstance.title)).toBeInTheDocument()
    const items = within(headingList(HEADINGS.releases)).getAllByRole('listitem')
    expect(items.map((item) => [
      item.querySelector('p')?.textContent, item.querySelector('p')?.nextElementSibling?.tagName, item.querySelector('dt')?.textContent,
      item.querySelector('dd')?.textContent, item.querySelector('a'),
    ])).toEqual([
      [`${isolateText('Synthetic extras x0 (superseded) (synthetic)')}: ${PRIMARY}`, 'DL', LICENCE, NO_LICENCE, null],
      [`${isolateText('Test labelled release x1')}: ${CONTEXT}`, 'DL', LICENCE, NO_LICENCE, null],
    ])
    const inHeader = header().textContent ?? ''
    for (const words of FULL_LIST_LINES) expect(inHeader).not.toContain(words)
    expect(asked()).toEqual([STATUS, instancePath(twoReleaseInstance)])
    view.unmount()
    server.resetHandlers()
    serve(stagedInstance)
    const before = asked().length
    renderGraphRoute('/browsers/syn-staged')
    expect(await title(stagedInstance.title)).toBeInTheDocument()
    expect(within(header()).getByText(STAGED_STATE)).toBeInTheDocument()
    const staged = within(headingList(HEADINGS.releases)).getAllByRole('listitem')
    expect(staged).toHaveLength(1)
    expect(staged[0].querySelector('p')?.textContent)
      .toBe(`${isolateText('Synthetic staged extras x2 (staged: not yet published) (synthetic)')}: ${PRIMARY}`)
    expect(staged[0].querySelector('dt')?.textContent).toBe(LICENCE)
    expect(staged[0].querySelector('dd')?.textContent).toBe('Test licence (CC-BY-4.0) https://example.org/licence')
    expect(within(staged[0]).getByRole('link', { name: 'https://example.org/licence' })).toHaveAttribute('href', 'https://example.org/licence')
    expect(asked().slice(before)).toEqual([STATUS, instancePath(stagedInstance)])
  }, 20_000)

  it('IP-8: the organisms, primary first, by name when the vocabularies know the taxid', async () => {
    const cases: [GraphInstance, string[] | null][] = [
      [externalInstance, ['Drosophila melanogaster (taxid 7227)', 'Homo sapiens (taxid 9606)']],
      [primaryOutsideInstance, ['Homo sapiens (taxid 9606)', SYNTHETIC_TAXON]],
      [unknownTaxonInstance, ['taxid 424242']],
      [plannedPortalInstance, null],
    ]
    for (const [instance, organisms] of cases) {
      server.resetHandlers()
      serve(instance)
      const before = asked().length
      const view = renderGraphRoute(`/browsers/${instance.slug}`)
      expect(await title(instance.title)).toBeInTheDocument()
      if (organisms === null) expect(screen.queryByRole('heading', { name: HEADINGS.organisms })).toBeNull()
      else expect(within(headingList(HEADINGS.organisms)).getAllByRole('listitem').map((item) => item.textContent), instance.slug).toEqual(organisms)
      expect(asked().slice(before)).toEqual([STATUS, instancePath(instance)])
      view.unmount()
    }
  }, 20_000)

  it('IP-9: the badge shows for a synthetic paper alone, and not for an instance with no synthetic part', async () => {
    serve(syntheticPaperInstance)
    const view = renderGraphRoute('/browsers/syn-paper-only')
    expect(await title(syntheticPaperInstance.title)).toBeInTheDocument()
    expect(screen.getByText(SYNTHETIC_BADGE)).toBeInTheDocument()
    expect(asked()).toEqual([STATUS, instancePath(syntheticPaperInstance)])
    view.unmount()
    server.resetHandlers()
    serve(plainInstance)
    const before = asked().length
    renderGraphRoute('/browsers/syn-plain')
    expect(await title(plainInstance.title)).toBeInTheDocument()
    expect(screen.queryByText(SYNTHETIC_BADGE)).toBeNull()
    expect(asked().slice(before)).toEqual([STATUS, instancePath(plainInstance)])
  }, 20_000)

  it('IP-10: a resolved term adds its entity once, a new submit while it is chosen writes nothing, the line follows a removal, and a new submit adds it again from the cache', async () => {
    const user = userEvent.setup()
    const { router } = renderGraphRoute('/browsers/synthetic-regulation')
    expect(await title(TYPED_TITLE)).toBeInTheDocument()
    // The line is mounted from the first render, empty while idle; typing changes nothing on it.
    const line = seedLine()
    expect(line.textContent).toBe('')
    await user.type(seedBox(), 'SYN-TF1')
    expect(line.textContent).toBe('')
    const watch = watchTexts(line)
    await user.keyboard('{Enter}')
    await waitFor(() => expect(asked()).toContain(RESOLVE_TF1), GRAPH_WAIT)
    await lineIs(chosenText(TF1))
    watch.stop()
    expect(seedLine()).toBe(line)
    expect(watch.texts).toContain(SEARCHING)
    expect(watch.texts.filter((text) => text === notChosenText(TF1))).toEqual([])
    expect(seeds(router)).toBe('entity:7')
    expect(router.state.historyAction).toBe('PUSH')
    await waitFor(() => expect(asked()).toContain(NETWORK_TF1), GRAPH_WAIT)
    expect(screen.getByRole('heading', { level: 2, name: NETWORK_HEADING })).toBeInTheDocument()
    const items = chosenItems()
    expect(items).toHaveLength(1)
    expect(within(items[0]).getByText(TF1)).toBe(items[0])
    expect(within(items[0]).getByRole('button', { name: removeText(TF1) })).toBeInTheDocument()
    expect(screen.queryByText(NO_SEED_PROMPT)).toBeNull()
    expect(asked().filter((url) => url.includes('taxon='))).toEqual([RESOLVE_TF1])
    await waitFor(() => expect(answers.answers).toContain(`200 ${NETWORK_TF1}`), GRAPH_WAIT)
    // The page ignores an entity already chosen, so a new submit of its term, which adds it again, writes nothing.
    const key = router.state.location.key
    await user.keyboard('{Enter}')
    // A pause after which nothing more is expected: that add must leave the address and its history entry as they were.
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 300))
    })
    expect(router.state.location.key).toBe(key)
    expect(seeds(router)).toBe('entity:7')
    expect(chosenItems()).toHaveLength(1)
    expect(seedLine().textContent).toBe(chosenText(TF1))
    await user.click(screen.getByRole('button', { name: REMOVE_ALL }))
    await waitFor(() => expect(seeds(router)).toBeNull(), GRAPH_WAIT)
    expect(document.activeElement).toBe(seedBox())
    await lineIs(notChosenText(TF1))
    expect(screen.getByText(NO_SEED_PROMPT)).toBeInTheDocument()
    // Both answers are cached, so a new submit adds the entity again with no request.
    const before = asked().length
    await user.keyboard('{Enter}')
    await waitFor(() => expect(seeds(router)).toBe('entity:7'), GRAPH_WAIT)
    await lineIs(chosenText(TF1))
    expect(asked().slice(before)).toEqual([])
    expect(asked()).toEqual([STATUS, INSTANCE, RESOLVE_TF1, NETWORK_TF1])
  }, 20_000)

  it('IP-11: a candidate is offered, never added by itself, and its button stays, off while chosen and on again once removed', async () => {
    const user = userEvent.setup()
    const { router } = renderGraphRoute('/browsers/synthetic-regulation')
    expect(await title(TYPED_TITLE)).toBeInTheDocument()
    await user.type(seedBox(), 'syn-tf1{Enter}')
    const add = await screen.findByRole('button', { name: addText(TF1) }, GRAPH_WAIT)
    expect(seedLine().textContent).toBe(candidateText('syn-tf1'))
    expect(candidateButtons()).toEqual([add])
    expect(seeds(router)).toBeNull()
    expect(router.state.location.search).toBe('')
    await user.click(add)
    await waitFor(() => expect(seeds(router)).toBe('entity:7'), GRAPH_WAIT)
    const kept = screen.getByRole('button', { name: addText(TF1) })
    expect(kept).toBe(add)
    await waitFor(() => expect(kept).toHaveAttribute('aria-disabled', 'true'), GRAPH_WAIT)
    expect(kept.style.borderStyle).toBe('dashed')
    expect(document.activeElement).toBe(kept)
    // Never `disabled` (8.2): jsdom keeps focus on a disabled button, where a browser takes it away.
    expect(kept).toBeEnabled()
    expect(kept.parentElement?.textContent).toBe(`${addText(TF1)}${CHOSEN_MARK}`)
    await lineIs(`${candidateText('syn-tf1')} ${chosenText(TF1)}`)
    expect((seedLine().textContent ?? '').endsWith(chosenText(TF1))).toBe(true)
    await waitFor(() => expect(answers.answers).toContain(`200 ${NETWORK_TF1}`), GRAPH_WAIT)
    await user.click(screen.getByRole('button', { name: removeText(TF1) }))
    await waitFor(() => expect(seeds(router)).toBeNull(), GRAPH_WAIT)
    await waitFor(() => expect(kept).not.toHaveAttribute('aria-disabled'), GRAPH_WAIT)
    expect(document.body.contains(kept)).toBe(true)
    expect(kept.style.borderStyle).toBe('solid')
    expect(kept.parentElement?.textContent).toBe(addText(TF1))
    // Removed, the picked entity is no longer chosen, so the candidate's sentence stands alone (8.2's pickedName).
    expect(seedLine().textContent).toBe(candidateText('syn-tf1'))
    expect(asked()).toEqual([STATUS, INSTANCE, RESOLVE_CASE, NETWORK_TF1])
  }, 20_000)

  it('IP-12: an ambiguous term asks for a choice, a second seed is named by the search at once, and removals move focus to the box', async () => {
    const user = userEvent.setup()
    const { router } = renderGraphRoute('/browsers/synthetic-regulation')
    expect(await title(TYPED_TITLE)).toBeInTheDocument()
    await user.type(seedBox(), 'SYN-SHARED{Enter}')
    const protein = await screen.findByRole('button', { name: addText(SHARED_PROTEIN) }, GRAPH_WAIT)
    expect(seedLine().textContent).toBe(ambiguousText('SYN-SHARED'))
    expect(candidateButtons().map((node) => node.textContent)).toEqual([addText(SHARED_GENE), addText(SHARED_PROTEIN)])
    await user.click(protein)
    await waitFor(() => expect(seeds(router)).toBe('entity:13'), GRAPH_WAIT)
    await waitFor(() => expect(asked()).toContain(NETWORK_SHARED), GRAPH_WAIT)
    await lineIs(`${ambiguousText('SYN-SHARED')} ${chosenText(SHARED_PROTEIN)}`)
    expect((seedLine().textContent ?? '').endsWith(chosenText(SHARED_PROTEIN))).toBe(true)
    await waitFor(() => expect(answers.answers).toContain(`200 ${NETWORK_SHARED}`), GRAPH_WAIT)
    await user.clear(seedBox())
    await user.type(seedBox(), 'SYN-TF1{Enter}')
    await waitFor(() => expect(seeds(router)).toBe('entity:13,entity:7'), GRAPH_WAIT)
    // Before the debounced request for both seeds goes out, the new seed is already named by the search.
    await waitFor(() => expect(chosenItems()).toHaveLength(2), GRAPH_WAIT)
    expect(asked()).not.toContain(NETWORK_BOTH)
    expect(chosenItems().map((item) => within(item).getByRole('button').textContent)).toEqual([removeText(SHARED_PROTEIN), removeText(TF1)])
    expect(chosenItems().map((item) => (item.firstChild as Text).textContent)).toEqual([SHARED_PROTEIN, TF1])
    await waitFor(() => expect(asked()).toContain(NETWORK_BOTH), GRAPH_WAIT)
    await waitFor(() => expect(answers.answers).toContain(`200 ${NETWORK_BOTH}`), GRAPH_WAIT)
    await user.click(screen.getByRole('button', { name: removeText(SHARED_PROTEIN) }))
    await waitFor(() => expect(seeds(router)).toBe('entity:7'), GRAPH_WAIT)
    expect(document.activeElement).toBe(seedBox())
    // The remaining seed's network goes out after F2's debounce, before the last removal takes the section away.
    await waitFor(() => expect(asked()).toContain(NETWORK_TF1), GRAPH_WAIT)
    await waitFor(() => expect(answers.answers).toContain(`200 ${NETWORK_TF1}`), GRAPH_WAIT)
    await user.click(screen.getByRole('button', { name: REMOVE_ALL }))
    await waitFor(() => expect(seeds(router)).toBeNull(), GRAPH_WAIT)
    expect(document.activeElement).toBe(seedBox())
    await waitFor(() => expect(screen.queryByRole('heading', { name: NETWORK_HEADING })).toBeNull(), GRAPH_WAIT)
    expect(screen.getByText(NO_SEED_PROMPT)).toBeInTheDocument()
    await lineIs(notChosenText(TF1))
    expect(asked()).toEqual([STATUS, INSTANCE, RESOLVE_SHARED, NETWORK_SHARED, RESOLVE_TF1, NETWORK_BOTH, NETWORK_TF1])
  }, 20_000)

  it('IP-13: an unmatched term names the organism, a comma is refused unsent, and an empty box does nothing', async () => {
    const user = userEvent.setup()
    const { router } = renderGraphRoute('/browsers/synthetic-regulation')
    expect(await title(TYPED_TITLE)).toBeInTheDocument()
    await user.type(seedBox(), 'SYN-NOPE{Enter}')
    await lineIs(unmatchedText('SYN-NOPE', SYNTHETIC_ORGANISM))
    expect(seeds(router)).toBeNull()
    expect(asked().filter((url) => url.startsWith('/api/graph/v1/network'))).toEqual([])
    let before = asked().length
    await user.clear(seedBox())
    await user.type(seedBox(), 'a,b{Enter}')
    await lineIs(ONE_TERM)
    expect(asked().slice(before)).toEqual([])
    before = asked().length
    await user.clear(seedBox())
    await user.keyboard('{Enter}')
    // A pause after which nothing more is expected: an empty box sends nothing and leaves the line as it was.
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 300))
    })
    expect(asked().slice(before)).toEqual([])
    expect(seedLine().textContent).toBe(ONE_TERM)
    expect(asked()).toEqual([STATUS, INSTANCE, RESOLVE_NOPE])
  }, 20_000)

  it.each([
    '?seeds=entity:7,entity:7&utm_source=news',
    '?seeds=bad&utm_source=news',
    '?seeds=&utm_source=news',
    `?seeds=${Array.from({ length: 11 }, (_, index) => `entity:${index + 1}`).join(',')}&utm_source=news`,
    '?seeds=entity:99999999999999999999&utm_source=news',
  ])('IP-14: the address %s reads as unusable, links to itself without seeds and keeps the way back, and sends no network request', async (search) => {
    const user = userEvent.setup()
    const { router } = renderGraphRoute(`/browsers/synthetic-regulation${search}`, { state: { gallerySearch: '?tag=gene_regulation' } })
    expect(await title(TYPED_TITLE)).toBeInTheDocument()
    expect(screen.getByText(SEEDS_ADDRESS_ERROR)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: SEEDS_REMOVAL })).toHaveAttribute('href', '/browsers/synthetic-regulation?utm_source=news')
    expect(screen.queryByRole('search', { name: SEED_FORM_NAME })).toBeNull()
    expect(screen.queryByRole('heading', { name: NETWORK_HEADING })).toBeNull()
    expect(asked()).toEqual([STATUS, INSTANCE])
    if (search !== '?seeds=bad&utm_source=news') return
    // The link takes itself away, so it hands focus to the section's heading first.
    const heading = screen.getByRole('heading', { level: 2, name: SEED_SECTION_HEADING })
    await user.click(screen.getByRole('link', { name: SEEDS_REMOVAL }))
    await waitFor(() => expect(router.state.location.search).toBe('?utm_source=news'), GRAPH_WAIT)
    await waitFor(() => expect(screen.getByRole('search', { name: SEED_FORM_NAME })).toBeInTheDocument(), GRAPH_WAIT)
    expect(screen.getByRole('heading', { level: 2, name: SEED_SECTION_HEADING })).toBe(heading)
    expect(document.activeElement).toBe(heading)
    expect(screen.queryByRole('link', { name: SEEDS_REMOVAL })).toBeNull()
    // The link carries the router state, so the way back still reaches the filtered gallery.
    expect(router.state.location.state).toEqual({ gallerySearch: '?tag=gene_regulation' })
    expect(screen.getByRole('link', { name: BACK_LINK })).toHaveAttribute('href', '/browsers?tag=gene_regulation')
    expect(asked()).toEqual([STATUS, INSTANCE])
  }, 20_000)

  it('IP-15: the seed limit from status turns every candidate off and bounds the address; no limits, or a limit that is no safe integer of at least 1, read the fallback', async () => {
    const user = userEvent.setup()
    statusWith((body) => ({ ...body, limits: { ...body.limits, max_seeds: 1 } }))
    let view = renderGraphRoute('/browsers/synthetic-regulation?seeds=entity%3A7')
    expect(await title(TYPED_TITLE)).toBeInTheDocument()
    await waitFor(() => expect(asked()).toContain(NETWORK_TF1), GRAPH_WAIT)
    await user.type(seedBox(), 'SYN-SHARED{Enter}')
    await screen.findByRole('list', { name: CANDIDATES_NAME }, GRAPH_WAIT)
    expect(seedLine().textContent).toBe(`${ambiguousText('SYN-SHARED')} ${LIMIT_ONE}`)
    const buttons = candidateButtons()
    expect(buttons.map((node) => node.getAttribute('aria-disabled'))).toEqual(['true', 'true'])
    for (const node of buttons) expect(node).toBeEnabled()
    await user.click(buttons[0])
    expect(seeds(view.router)).toBe('entity:7')
    expect(view.router.state.location.search).toBe('?seeds=entity%3A7')
    expect(view.router.state.historyAction).toBe('POP')
    await waitFor(() => expect(answers.answers).toContain(`200 ${NETWORK_TF1}`), GRAPH_WAIT)
    expect(asked()).toEqual([STATUS, INSTANCE, NETWORK_TF1, RESOLVE_SHARED])
    view.unmount()
    // The same limit bounds the address: two seeds are more than 1, so they read as unusable and ask for no network.
    let before = asked().length
    view = renderGraphRoute('/browsers/synthetic-regulation?seeds=entity%3A7%2Centity%3A13')
    expect(await title(TYPED_TITLE)).toBeInTheDocument()
    expect(screen.getByText(SEEDS_ADDRESS_ERROR)).toBeInTheDocument()
    expect(screen.queryByRole('search', { name: SEED_FORM_NAME })).toBeNull()
    expect(asked().slice(before)).toEqual([STATUS, INSTANCE])
    view.unmount()
    server.resetHandlers()
    statusWith((body) => {
      const copy: Record<string, unknown> = { ...body }
      delete copy.limits
      return copy
    })
    before = asked().length
    let answeredBefore = answers.answers.length
    view = renderGraphRoute('/browsers/synthetic-regulation?seeds=entity%3A7%2Centity%3A13')
    expect(await title(TYPED_TITLE)).toBeInTheDocument()
    expect(screen.getByRole('search', { name: SEED_FORM_NAME })).toBeInTheDocument()
    await waitFor(() => expect(asked().slice(before)).toContain(NETWORK_BOTH), GRAPH_WAIT)
    await user.type(seedBox(), 'SYN-SHARED{Enter}')
    await screen.findByRole('list', { name: CANDIDATES_NAME }, GRAPH_WAIT)
    // Two seeds stand below the fallback: only the chosen Protein candidate is off, and no limit is read.
    expect(seedLine().textContent).toBe(ambiguousText('SYN-SHARED'))
    expect(candidateButtons().map((node) => node.getAttribute('aria-disabled'))).toEqual([null, 'true'])
    await waitFor(() => expect(answers.answers.slice(answeredBefore)).toContain(`200 ${NETWORK_BOTH}`), GRAPH_WAIT)
    expect(asked().slice(before)).toEqual([STATUS, INSTANCE, NETWORK_BOTH, RESOLVE_SHARED])
    view.unmount()
    // A limit below 1, or one that is no integer, reads the fallback too, so the same two seeds draw their network.
    for (const limit of [0, 1.5]) {
      server.resetHandlers()
      statusWith((body) => ({ ...body, limits: { ...body.limits, max_seeds: limit } }))
      before = asked().length
      answeredBefore = answers.answers.length
      view = renderGraphRoute('/browsers/synthetic-regulation?seeds=entity%3A7%2Centity%3A13')
      expect(await title(TYPED_TITLE)).toBeInTheDocument()
      expect(screen.queryByText(SEEDS_ADDRESS_ERROR), String(limit)).toBeNull()
      await waitFor(() => expect(answers.answers.slice(answeredBefore)).toContain(`200 ${NETWORK_BOTH}`), GRAPH_WAIT)
      expect(asked().slice(before), String(limit)).toEqual([STATUS, INSTANCE, NETWORK_BOTH])
      view.unmount()
    }
  }, 20_000)

  it('IP-16: outbound links carry the exact rel, no address escapes, no Explore, and a citation links its http(s) address', async () => {
    const check = () => {
      for (const link of Array.from(page().querySelectorAll('a[target="_blank"]'))) expect(link.getAttribute('rel')).toBe('noopener noreferrer')
      expect(strayAddresses()).toEqual([])
      const hrefs = Array.from(page().querySelectorAll('a[href]'), (link) => link.getAttribute('href') ?? '')
      expect(hrefs.filter((href) => href.startsWith('/explore') || href.startsWith('/entities'))).toEqual([])
      expect(page().textContent ?? '').not.toContain('Explore')
    }
    let view = renderGraphRoute('/browsers/synthetic-regulation?seeds=entity%3A7')
    expect(await title(TYPED_TITLE)).toBeInTheDocument()
    await screen.findByRole('button', { name: TF1_G1 }, GRAPH_WAIT)
    expect(page().querySelectorAll('a[target="_blank"]').length).toBeGreaterThan(0)
    check()
    expect(asked()).toEqual([STATUS, INSTANCE, NETWORK_TF1])
    view.unmount()
    serve(externalInstance)
    let before = asked().length
    view = renderGraphRoute('/browsers/syn-external')
    expect(await title(externalInstance.title)).toBeInTheDocument()
    expect(page().querySelectorAll('a[target="_blank"]')).toHaveLength(1)
    check()
    expect(asked().slice(before)).toEqual([STATUS, instancePath(externalInstance)])
    view.unmount()
    server.resetHandlers()
    serve(papersInstance)
    before = asked().length
    renderGraphRoute('/browsers/syn-papers')
    expect(await title(papersInstance.title)).toBeInTheDocument()
    check()
    const link = screen.getByRole('link', { name: 'https://example.org/paper-1' })
    expect(link).toHaveAttribute('href', 'https://example.org/paper-1')
    expect(link).toHaveAttribute('target', '_blank')
    expect(link.getAttribute('rel')).toBe('noopener noreferrer')
    expect(screen.getByText(`${isolateText(NO_TITLE)}${SYNTHETIC_SUFFIX}`).closest('a')).toBeNull()
    expect(asked().slice(before)).toEqual([STATUS, instancePath(papersInstance)])
  }, 20_000)

  it('IP-17: a cross-taxon instance sends no taxon and its unmatched line names no organism', async () => {
    const user = userEvent.setup()
    serve(crossTaxonInstance)
    server.use(http.get('/api/graph/v1/resolve', () => HttpResponse.json(recorded<JsonBodyType>('resolve-browser-nope').body)))
    renderGraphRoute('/browsers/syn-cross')
    expect(await title(crossTaxonInstance.title)).toBeInTheDocument()
    await user.type(seedBox(), 'SYN-TF1{Enter}')
    await lineIs(unmatchedText('SYN-TF1', null))
    expect(asked()).toEqual([STATUS, instancePath(crossTaxonInstance), '/api/graph/v1/resolve?q=SYN-TF1&browser=syn-cross'])
  }, 20_000)

  it("IP-18: the way back keeps the gallery's search, before and after a seed is added", async () => {
    const user = userEvent.setup()
    const { router } = renderGraphRoute('/browsers/synthetic-regulation', { state: { gallerySearch: '?tag=gene_regulation' } })
    expect(await title(TYPED_TITLE)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: BACK_LINK })).toHaveAttribute('href', '/browsers?tag=gene_regulation')
    await user.type(seedBox(), 'SYN-TF1{Enter}')
    await waitFor(() => expect(seeds(router)).toBe('entity:7'), GRAPH_WAIT)
    await waitFor(() => expect(answers.answers).toContain(`200 ${NETWORK_TF1}`), GRAPH_WAIT)
    expect(router.state.location.state).toEqual({ gallerySearch: '?tag=gene_regulation' })
    expect(screen.getByRole('link', { name: BACK_LINK })).toHaveAttribute('href', '/browsers?tag=gene_regulation')
    expect(asked()).toEqual([STATUS, INSTANCE, RESOLVE_TF1, NETWORK_TF1])
  }, 20_000)

  it('IP-19: a search answered by a switched-off portal rechecks status once, and the gate then says not enabled', async () => {
    const user = userEvent.setup()
    renderGraphRoute('/browsers/synthetic-regulation')
    expect(await title(TYPED_TITLE)).toBeInTheDocument()
    server.use(...typedGraphOffHandlers)
    const before = asked().length
    await user.type(seedBox(), 'SYN-TF1{Enter}')
    await waitFor(() => expect(asked().slice(before)).toContain(STATUS), GRAPH_WAIT)
    expect(await screen.findByText(NOT_ENABLED, {}, GRAPH_WAIT)).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 1, name: TITLE })).toBeInTheDocument()
    expect(answers.answers).toContain(`404 ${RESOLVE_TF1}`)
    expect(asked().slice(before)).toEqual([RESOLVE_TF1, STATUS])
  }, 20_000)

  it('IP-20: an add refused at the limit reads the limit; once a seed goes, a new submit adds it from the cache', async () => {
    const user = userEvent.setup()
    statusWith((body) => ({ ...body, limits: { ...body.limits, max_seeds: 1 } }))
    const { router } = renderGraphRoute('/browsers/synthetic-regulation?seeds=entity%3A13')
    expect(await title(TYPED_TITLE)).toBeInTheDocument()
    await waitFor(() => expect(asked()).toContain(NETWORK_SHARED), GRAPH_WAIT)
    await user.type(seedBox(), 'SYN-TF1{Enter}')
    await lineIs(LIMIT_ONE)
    expect(seeds(router)).toBe('entity:13')
    // Once the network answers, the seed taken from the address is named by its node.
    await waitFor(() => expect(answers.answers).toContain(`200 ${NETWORK_SHARED}`), GRAPH_WAIT)
    await user.click(await screen.findByRole('button', { name: removeText(SHARED_PROTEIN) }, GRAPH_WAIT))
    await waitFor(() => expect(seeds(router)).toBeNull(), GRAPH_WAIT)
    await lineIs(notChosenText(TF1))
    expect(document.activeElement).toBe(seedBox())
    const before = asked().length
    await user.keyboard('{Enter}')
    await waitFor(() => expect(seeds(router)).toBe('entity:7'), GRAPH_WAIT)
    await lineIs(chosenText(TF1))
    await waitFor(() => expect(answers.answers).toContain(`200 ${NETWORK_TF1}`), GRAPH_WAIT)
    expect(asked().slice(before)).toEqual([NETWORK_TF1])
    expect(asked()).toEqual([STATUS, INSTANCE, NETWORK_SHARED, RESOLVE_TF1, NETWORK_TF1])
  }, 20_000)

  it.each([
    '/browsers/Synthetic-Regulation',
    '/browsers/a%2Fb',
    `/browsers/${'a'.repeat(51)}`,
    '/browsers/..',
  ])('IP-21: %s is outside the slug grammar: not available, no Try again, no request and no recheck', async (path) => {
    renderGraphRoute(path)
    expect(await screen.findByText(NOT_AVAILABLE, {}, GRAPH_WAIT)).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 1, name: TITLE })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: TRY_AGAIN })).toBeNull()
    // A pause after which nothing more is expected: neither the instance nor a recheck may be asked.
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 300))
    })
    expect(asked()).toEqual([STATUS])
  }, 20_000)

  it("IP-22: a candidate's hostile label shows as literal text in its button", async () => {
    const user = userEvent.setup()
    const body = recorded<GraphResolveResponse>('resolve-browser-tf1-case').body
    const candidate = body.results[0].candidates[0]
    const stub = {
      ...body,
      results: [{ ...body.results[0], candidates: [{ ...candidate, entity: { ...candidate.entity, label: `SYN-${hostile('label')}` } }] }],
    }
    server.use(http.get('/api/graph/v1/resolve', () => HttpResponse.json(stub)))
    const { container } = renderGraphRoute('/browsers/synthetic-regulation')
    expect(await title(TYPED_TITLE)).toBeInTheDocument()
    await user.type(seedBox(), 'syn-tf1{Enter}')
    const button = await screen.findByRole('button', { name: addText(`SYN-${hostile('label')} (Gene)`) }, GRAPH_WAIT)
    expect(button.textContent).toBe(addText(`SYN-${hostile('label')} (Gene)`))
    expect(container.querySelector('[data-hostile]')).toBeNull()
    expect(asked()).toEqual([STATUS, INSTANCE, RESOLVE_CASE])
  }, 20_000)

  it('IP-23: a body the search refuses reads could not be run, and Search sends it again', async () => {
    const user = userEvent.setup()
    const body = recorded<GraphResolveResponse>('resolve-browser-tf1').body
    const first = body.results[0]
    const noEntity: Record<string, unknown> = { ...first.candidates[0] }
    delete noEntity.entity
    const bad = { ...body, results: [{ ...first, candidates: [noEntity] }] }
    server.use(http.get('/api/graph/v1/resolve', () => HttpResponse.json(bad)))
    const { router } = renderGraphRoute('/browsers/synthetic-regulation')
    expect(await title(TYPED_TITLE)).toBeInTheDocument()
    const sent = () => asked().filter((url) => url === RESOLVE_TF1)
    const answered = () => answers.answers.filter((answer) => answer === `200 ${RESOLVE_TF1}`)
    await user.type(seedBox(), 'SYN-TF1{Enter}')
    await waitFor(() => expect(answered()).toHaveLength(1), GRAPH_WAIT)
    await lineIs(SEARCH_FAILED_AGAIN)
    expect(sent()).toHaveLength(1)
    // An unchanged key sends nothing by itself: the press asks again.
    await user.keyboard('{Enter}')
    await waitFor(() => expect(sent()).toHaveLength(2), GRAPH_WAIT)
    await waitFor(() => expect(answered()).toHaveLength(2), GRAPH_WAIT)
    await lineIs(SEARCH_FAILED_AGAIN)
    // A refused term in between is no answer to this one: one press for the failed term sends it a third time.
    const before = asked().length
    await user.clear(seedBox())
    await user.type(seedBox(), 'a,b{Enter}')
    await lineIs(ONE_TERM)
    expect(asked().slice(before)).toEqual([])
    await user.clear(seedBox())
    await user.type(seedBox(), 'SYN-TF1{Enter}')
    await waitFor(() => expect(sent()).toHaveLength(3), GRAPH_WAIT)
    await waitFor(() => expect(answered()).toHaveLength(3), GRAPH_WAIT)
    await lineIs(SEARCH_FAILED_AGAIN)
    expect(seeds(router)).toBeNull()
    expect(asked()).toEqual([STATUS, INSTANCE, RESOLVE_TF1, RESOLVE_TF1, RESOLVE_TF1])
  }, 20_000)

  it('IP-24: a cached instance whose refetch fails keeps its header, says so beside Try again, and a retry that succeeds empties the line', async () => {
    const user = userEvent.setup()
    const { client } = renderGraphRoute('/browsers/synthetic-regulation')
    const heading = await title(TYPED_TITLE)
    expect(routeLine().textContent).toBe('')
    expect(screen.queryByRole('button', { name: TRY_AGAIN })).toBeNull()
    let calls = 0
    const second = held()
    server.use(http.get(INSTANCE, async () => {
      calls += 1
      if (calls === 1) return HttpResponse.json({ detail: 'x' }, { status: 500 })
      await second.promise
      return HttpResponse.json(recorded<GraphBrowserResponse>('browser-typed').body)
    }))
    // The instance's key, matched by its prefix (API/browsers.ts).
    await act(async () => {
      await client.refetchQueries({ queryKey: ['graph', 'browsers', 'detail', 'synthetic-regulation'] })
    })
    expect(screen.getByRole('heading', { level: 1, name: TYPED_TITLE })).toBe(heading)
    expect(routeLine().compareDocumentPosition(header()) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    await waitFor(() => expect(routeLine().textContent).toBe(BROWSER_ERROR), GRAPH_WAIT)
    const retry = screen.getByRole('button', { name: TRY_AGAIN })
    await user.click(retry)
    await waitFor(() => expect(retry).toHaveAttribute('aria-disabled', 'true'), GRAPH_WAIT)
    expect(document.activeElement).toBe(retry)
    expect(retry).toBeEnabled()
    expect(routeLine().textContent).toBe(BROWSER_ERROR)
    expect(screen.getByRole('heading', { level: 1, name: TYPED_TITLE })).toBe(heading)
    second.release()
    await waitFor(() => expect(routeLine().textContent).toBe(''), GRAPH_WAIT)
    await waitFor(() => expect(screen.queryByRole('button', { name: TRY_AGAIN })).toBeNull(), GRAPH_WAIT)
    await waitFor(() => expect(document.activeElement).toBe(heading), GRAPH_WAIT)
    expect(calls).toBe(2)
    expect(asked()).toEqual([STATUS, INSTANCE, INSTANCE, INSTANCE])
  }, 20_000)

  it('IP-25: a search sent offline reads as searching, a press sends nothing more, and the reconnect adds its entity', async () => {
    const user = userEvent.setup()
    const { router } = renderGraphRoute('/browsers/synthetic-regulation')
    expect(await title(TYPED_TITLE)).toBeInTheDocument()
    const before = asked().length
    act(() => {
      onlineManager.setOnline(false)
    })
    try {
      await user.type(seedBox(), 'SYN-TF1{Enter}')
      await lineIs(SEARCHING)
      // F1's query waits for the network: a press meanwhile asks nothing again, and nothing goes out.
      await user.keyboard('{Enter}')
      await act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 300))
      })
      expect(seedLine().textContent).toBe(SEARCHING)
      expect(asked().slice(before)).toEqual([])
      expect(seeds(router)).toBeNull()
    } finally {
      await act(async () => {
        onlineManager.setOnline(true)
      })
    }
    await waitFor(() => expect(seeds(router)).toBe('entity:7'), GRAPH_WAIT)
    await lineIs(chosenText(TF1))
    await waitFor(() => expect(answers.answers).toContain(`200 ${NETWORK_TF1}`), GRAPH_WAIT)
    expect(asked().slice(before)).toEqual([RESOLVE_TF1, NETWORK_TF1])
  }, 20_000)

  it('IP-26: a body that names another slug, or none, reads could not be loaded and sends nothing under it', async () => {
    const body = recorded<GraphBrowserResponse>('browser-typed').body
    for (const other of ['', 'protein-interactions']) {
      server.resetHandlers()
      server.use(http.get(INSTANCE, () => HttpResponse.json({ ...body, instance: { ...body.instance, slug: other } })))
      const before = asked().length
      const view = renderGraphRoute('/browsers/synthetic-regulation?seeds=entity%3A7')
      expect(await screen.findByText(BROWSER_ERROR, {}, GRAPH_WAIT)).toBeInTheDocument()
      expect(screen.getByRole('button', { name: TRY_AGAIN })).toBeInTheDocument()
      expect(screen.queryByRole('heading', { level: 1, name: TYPED_TITLE })).toBeNull()
      expect(screen.queryByRole('heading', { name: SEED_SECTION_HEADING })).toBeNull()
      expect(asked().slice(before)).toEqual([STATUS, INSTANCE])
      view.unmount()
    }
  }, 20_000)
})
