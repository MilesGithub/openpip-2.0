import { act, cleanup, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import type { JsonBodyType } from 'msw'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import type { MockInstance } from 'vitest'
import { makeResolveResponse, namespaceAliasResolve, namespaceAmbiguousResolve } from '../../mocks/fixtures/typedGraph/extras'
import { typedGraphOffHandlers } from '../../mocks/handlers/typedGraph'
import { server } from '../../mocks/server'
import { useAuthStore } from '../../store/authStore'
import { GRAPH_WAIT, recordRequests, recorded } from '../../test/graphHooks'
import type { RequestLog } from '../../test/graphHooks'
import { recordAnswers, renderGraphRoute } from '../../test/graphRoutes'
import type { GraphEntityResponse, GraphRelationshipResponse, GraphResolveResponse, GraphStatus } from '../graph/types'
import { isolateText } from '../graph/words/isolateText'

// X1 spec 12.10 (XR and XL): the two entity routes; no canvas renders on these pages.
let requests: RequestLog
let answers: { answers: string[]; stop: () => void }
let scrolls: MockInstance

beforeAll(async () => {
  await import('./EntityPages')
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

// Words copied from X1's spec, sections 10.10, 11.4 and 7.12, with F3's and F4's of 12.0, never from the modules under test.
const i = isolateText
const ENTITY_TITLE = 'Entity'
const ENTITY_NOT_ENABLED = 'Entity pages are not enabled on this portal.'
const ENTITY_LOOKUP_TITLE = 'Find an entity by its identifier'
const ENTITY_LOOKUP_NOT_ENABLED = 'Finding an entity by its identifier is not enabled on this portal.'
const ENTITY_ADDRESS_PROBLEM = "This address names no entity: an entity page's address ends with entity: and a number, such as entity:7."
const EXPLORE_ENTITY = 'Explore this entity in the unified network'
const LOOKUP_SEARCHING = 'Finding the entity…'
const LOOKUP_CANDIDATES_NAME = 'Matching entities'
const EMPTY_VALUE = 'This address holds no identifier to look up.'
const NAMESPACE_PROBLEM = 'This portal uses no identifier namespace of that name.'
const LOOKUP_ERROR = 'This identifier could not be looked up.'
const selectionProblemText = (nouns: string) => `This page cannot use the ${nouns} named in this address.`
const selectionRemovalText = (nouns: string) => `Show this page without the ${nouns}`
const openText = (name: string) => `Open ${i(name)}`
const lookupSubjectText = (label: string | null, namespace: string, value: string) =>
  (label !== null ? `${label} “${i(value)}”` : `“${i(value)}” in the namespace “${i(namespace)}”`)
const lookupCandidateText = (value: string) => `“${i(value)}” matches one entity, though not as its primary identifier is written. Open it if it is the one you meant.`
const lookupAmbiguousText = (value: string) => `“${i(value)}” matches more than one entity. Open the one you meant.`
const lookupUnmatchedText = (value: string, label: string) => `No entity available to you has the ${label} “${i(value)}”.`
const separatorValueText = (value: string) =>
  `“${i(value)}” holds a comma, a tab or a line break, which would make it more than one identifier, so it was not looked up.`
const controlValueText = (value: string) => `“${i(value)}” holds a control character, so it was not looked up.`
const longValueText = (value: string, max: number) => `“${i(value)}” is longer than ${max} characters, so it was not looked up.`
const BADGE_LINE = 'Synthetic demonstration data Made up to demonstrate openPIP; it describes no real biology.'
const SYNTHETIC_BADGE = 'Synthetic demonstration data'
const SYNTHETIC_LEGEND = 'Synthetic demonstration data: made up to demonstrate openPIP; it describes no real biology.'
const PAGE_UNKNOWN = 'This page could not be loaded.'
const RELOAD = 'Reload'
const TRY_AGAIN = 'Try again'
const BAD_BODY = "The server's answer could not be read."
const PROVISIONAL = "Status: provisional (created from a release's evidence; no reference release has stated this identifier yet)."
const NOT_AVAILABLE = 'This entity is not available to you.'
const TRY_ENTITY_AGAIN = 'Try loading this entity again'
const LATER = 'This entity could not be loaded now. Try again later.'
const THROTTLED_SEVEN = 'Too many requests. Try again in 7 seconds.'
const CLOSE = 'Close'
const FACTS_CASE = 'synthetic construct (taxid 32630); matched by its primary identifier in another case'
const FACTS_ALIAS = 'synthetic construct (taxid 32630); matched by an alias'
const ENCODES_ROW = 'Encodes: SYN-TF1 (Gene) to SYN-P1 (Protein (UniProtKB entry))'
const TF1 = 'SYN-TF1 (Gene)'
const P1 = 'SYN-P1 (Protein (UniProtKB entry))'
const GENE_ID = 'Synthetic gene ID'

const STATUS = '/api/graph/v1/status'
const ENTITY_7 = '/api/graph/v1/entities/entity%3A7'
const ENTITY_7_BROWSER = '/api/graph/v1/entities/entity%3A7?browser=synthetic-regulation'
const ENTITY_10 = '/api/graph/v1/entities/entity%3A10'
const ENTITY_11 = '/api/graph/v1/entities/entity%3A11'
const RELATIONSHIP_1 = '/api/graph/v1/relationships/relationship%3A1'
const EVIDENCE_1 = '/api/graph/v1/relationships/relationship%3A1/evidence'
const RESOLVE_NS_TF1 = '/api/graph/v1/resolve?q=SYN-TF1&namespace=synthetic_gene'
const RESOLVE_NS_CASE = '/api/graph/v1/resolve?q=syn-tf1&namespace=synthetic_gene'
const RESOLVE_NS_ALPHA = '/api/graph/v1/resolve?q=SYN-ALPHA&namespace=synthetic_gene'
const RESOLVE_NS_ISOFORM = '/api/graph/v1/resolve?q=SYN-P1-1&namespace=synthetic_protein'
const RESOLVE_NS_UNKNOWN = '/api/graph/v1/resolve?q=SYN-TF1&namespace=no_such_namespace'

// 12.0's helpers.
const page = () => document.querySelector('main') as HTMLElement
const h1 = (name: string) => screen.getByRole('heading', { level: 1, name })
/** F4's address check: every address inside the page is http(s), the scheme in any case, or a single-slash router path. */
const strayAddresses = () => Array.from(page().querySelectorAll('a[href]'), (link) => link.getAttribute('href') ?? '')
  .filter((href) => !/^(?:https?:\/\/|\/(?!\/))/i.test(href))
const hrefs = () => Array.from(page().querySelectorAll('a[href]'), (link) => link.getAttribute('href') ?? '')
const statusWith = (change: (body: GraphStatus) => JsonBodyType) =>
  server.use(http.get(STATUS, () => HttpResponse.json(change(recorded<GraphStatus>('status-on').body))))
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
const pause = (ms: number) => act(async () => {
  await new Promise((resolve) => setTimeout(resolve, ms))
})
// 12.10's helpers.
const lookupLine = () => within(page()).getByRole('status')
const candidates = () => within(screen.getByRole('list', { name: LOOKUP_CANDIDATES_NAME })).getAllByRole('listitem')
const entityRegion = (name: string) => screen.getByRole('region', { name })
const bodyShown = () => screen.findByText(PROVISIONAL, {}, GRAPH_WAIT)

const asked = () => requests.urls.map((url) => url.pathname + url.search)
const askedAfter = (n: number) => asked().slice(n)
const askedFor = (url: string) => waitFor(() => expect(asked()).toContain(url), GRAPH_WAIT)
const answered = (url: string) => waitFor(() => expect(answers.answers.filter((answer) => answer.endsWith(` ${url}`)).length).toBeGreaterThan(0), GRAPH_WAIT)
const copy = <T,>(value: T): T => JSON.parse(JSON.stringify(value)) as T
const following = (first: Node, second: Node) => (first.compareDocumentPosition(second) & Node.DOCUMENT_POSITION_FOLLOWING) !== 0
const badges = () => Array.from(page().querySelectorAll('p')).filter((node) => node.textContent === BADGE_LINE)
const tf1Body = () => recorded<GraphEntityResponse>('entity-tf1').body
/** Answers the requests of one path (search included, when given) with `answer`; any other falls through to the recordings. */
function serveAt(pathname: string, answer: (request: Request) => Response | undefined | Promise<Response | undefined>, route = '/api/graph/v1/entities/:id') {
  server.use(http.get(route, ({ request }) => (new URL(request.url).pathname === pathname ? answer(request) : undefined)))
}
function serveResolve(full: string, body: JsonBodyType | (() => Response)) {
  server.use(http.get('/api/graph/v1/resolve', ({ request }) => {
    const url = new URL(request.url)
    if (url.pathname + url.search !== full) return undefined
    return typeof body === 'function' ? body() : HttpResponse.json(body)
  }))
}
function recordFocus() {
  const focused: Element[] = []
  const record = (event: FocusEvent) => {
    focused.push(event.target as Element)
  }
  document.addEventListener('focusin', record)
  return { focused, stop: () => document.removeEventListener('focusin', record) }
}

describe('the entity page', () => {
  it('XR-1: the entity page is F3\'s panel at level one, with its badge and Explore link', async () => {
    for (const path of ['/entities/entity:7', '/entities/entity%3A7']) {
      const before = asked().length
      const { router } = renderGraphRoute(path)
      await bodyShown()
      const headings = screen.getAllByRole('heading', { level: 1 })
      expect(headings.map((node) => node.textContent)).toEqual([TF1])
      expect([headings[0].style.fontSize, headings[0].style.fontWeight, headings[0].style.scrollMarginTop]).toEqual(['28px', '600', '72px'])
      expect(screen.queryByRole('button', { name: CLOSE })).toBeNull()
      const first = page().querySelector('p') as HTMLElement
      expect(first).toHaveAttribute('aria-hidden', 'true')
      expect(first.textContent).toBe(SYNTHETIC_BADGE)
      const region = entityRegion(TF1)
      expect(following(first, region)).toBe(true)
      expect(within(region).getByText(SYNTHETIC_LEGEND)).toBeInTheDocument()
      expect(within(region).getByText('Taxon: synthetic construct (taxid 32630)')).toBeInTheDocument()
      expect(within(region).getByRole('heading', { level: 2, name: 'Reference relationships' })).toBeInTheDocument()
      expect(within(region).getByRole('button', { name: ENCODES_ROW })).toBeInTheDocument()
      const explore = screen.getByRole('link', { name: EXPLORE_ENTITY })
      expect(explore).toHaveAttribute('href', '/explore?v=1&seeds=entity%3A7')
      expect(following(region, explore)).toBe(true)
      await pause(1000)
      expect(askedAfter(before)).toEqual([STATUS, ENTITY_7])
      expect(router.state.location.pathname).toBe(path)
      expect(document.activeElement).toBe(document.body)
      expect(scrolls).not.toHaveBeenCalled()
      cleanup()
    }
  }, 20_000)

  it("XR-2: the entity's h1 takes focus only once the page's own request has answered", async () => {
    const entity = held()
    serveAt(ENTITY_7, async () => {
      await entity.promise
      return undefined
    })
    const { router } = renderGraphRoute('/')
    const focus = recordFocus()
    try {
      await act(() => router.navigate('/entities/entity%3A7'))
      await screen.findByRole('heading', { level: 1, name: 'Entity entity:7' }, GRAPH_WAIT)
      expect(document.activeElement).toBe(document.body)
      entity.release()
      const heading = await screen.findByRole('heading', { level: 1, name: TF1 }, GRAPH_WAIT)
      await waitFor(() => expect(document.activeElement).toBe(heading), GRAPH_WAIT)
      expect(focus.focused).toHaveLength(1)
      expect(focus.focused[0]).toBe(heading)
      expect(scrolls).toHaveBeenCalledTimes(1)
      expect(scrolls).toHaveBeenCalledWith(0, 0)
      await bodyShown()
    } finally {
      focus.stop()
    }
  }, 20_000)

  it('XR-3: with the network off there is no Explore link', async () => {
    statusWith((body) => ({ ...body, features: { ...body.features, network: false } }))
    renderGraphRoute('/entities/entity%3A7')
    await bodyShown()
    expect(screen.queryByRole('link', { name: EXPLORE_ENTITY })).toBeNull()
    expect(asked()).toEqual([STATUS, ENTITY_7])
  }, 20_000)

  it('XR-4: a reference relationship opens below, and its ends close it or open their own page', async () => {
    const user = userEvent.setup()
    const { router } = renderGraphRoute('/entities/entity%3A7')
    await bodyShown()
    const reference = () => within(entityRegion(TF1)).getByRole('button', { name: ENCODES_ROW })
    await user.click(reference())
    const region = await screen.findByRole('region', { name: ENCODES_ROW }, GRAPH_WAIT)
    const heading = within(region).getByRole('heading', { name: ENCODES_ROW })
    expect(heading.tagName).toBe('H2')
    await waitFor(() => expect(document.activeElement).toBe(heading), GRAPH_WAIT)
    expect(following(screen.getByRole('link', { name: EXPLORE_ENTITY }), region)).toBe(true)
    await askedFor(EVIDENCE_1)
    expect(asked()).toEqual([STATUS, ENTITY_7, RELATIONSHIP_1, EVIDENCE_1])
    await user.click(within(region).getByRole('button', { name: CLOSE }))
    expect(screen.queryByRole('region', { name: ENCODES_ROW })).toBeNull()
    await waitFor(() => expect(document.activeElement).toBe(reference()), GRAPH_WAIT)
    await user.click(reference())
    await screen.findByText('Open an end in its own panel:', {}, GRAPH_WAIT)
    await user.click(within(screen.getByRole('region', { name: ENCODES_ROW })).getByRole('button', { name: TF1 }))
    await waitFor(() => expect(screen.queryByRole('region', { name: ENCODES_ROW })).toBeNull(), GRAPH_WAIT)
    await waitFor(() => expect(document.activeElement).toBe(reference()), GRAPH_WAIT)
    await user.click(reference())
    await screen.findByText('Open an end in its own panel:', {}, GRAPH_WAIT)
    await user.click(within(screen.getByRole('region', { name: ENCODES_ROW })).getByRole('button', { name: P1 }))
    expect(router.state.historyAction).toBe('PUSH')
    expect(router.state.location.pathname).toBe('/entities/entity%3A10')
    await answered(ENTITY_10)
    expect(await screen.findByRole('heading', { level: 1, name: P1 }, GRAPH_WAIT)).toBeInTheDocument()
    await act(() => router.navigate(-1))
    expect(router.state.location.pathname).toBe('/entities/entity%3A7')
    expect(await screen.findByRole('heading', { level: 1, name: TF1 }, GRAPH_WAIT)).toBeInTheDocument()
    await bodyShown()
    expect(asked()).toEqual([STATUS, ENTITY_7, RELATIONSHIP_1, EVIDENCE_1, ENTITY_10])
  }, 20_000)

  it('XR-5: a merged entity shows its survivor under the address asked for', async () => {
    serveAt('/api/graph/v1/entities/entity%3A98', () => HttpResponse.json(tf1Body() as unknown as JsonBodyType))
    const { router } = renderGraphRoute('/entities/entity%3A98')
    expect(await screen.findByRole('heading', { level: 1, name: TF1 }, GRAPH_WAIT)).toBeInTheDocument()
    expect(await within(entityRegion(TF1)).findByText('The entity asked for, entity:98, was merged into this one.', {}, GRAPH_WAIT)).toBeInTheDocument()
    expect(await screen.findByRole('link', { name: EXPLORE_ENTITY }, GRAPH_WAIT)).toHaveAttribute('href', '/explore?v=1&seeds=entity%3A7')
    expect(router.state.location.pathname).toBe('/entities/entity%3A98')
    expect(asked()).toEqual([STATUS, '/api/graph/v1/entities/entity%3A98'])
  }, 20_000)

  it("XR-6: a 404 is worded, rechecks status, and the body's repeat is asserted one-sidedly", async () => {
    serveAt(ENTITY_7, () => HttpResponse.json({ detail: 'Not found.' }, { status: 404 }))
    renderGraphRoute('/entities/entity%3A7')
    expect(await screen.findByRole('heading', { level: 1, name: 'Entity entity:7' }, GRAPH_WAIT)).toBeInTheDocument()
    const region = entityRegion('Entity entity:7')
    await waitFor(() => expect(within(region).getByRole('status').textContent).toBe(NOT_AVAILABLE), GRAPH_WAIT)
    expect(screen.queryByRole('button', { name: TRY_ENTITY_AGAIN })).toBeNull()
    expect(screen.queryByRole('link', { name: EXPLORE_ENTITY })).toBeNull()
    expect(Array.from(page().querySelectorAll('p')).filter((node) => node.textContent === SYNTHETIC_BADGE)).toEqual([])
    await waitFor(() => expect(asked().length).toBeGreaterThanOrEqual(3), GRAPH_WAIT)
    expect(asked().slice(0, 3)).toEqual([STATUS, ENTITY_7, STATUS])
    await settled(1000)
    expect([[ENTITY_7, STATUS], []]).toContainEqual(asked().slice(3))
    expect(within(region).getByRole('status').textContent).toBe(NOT_AVAILABLE)
    cleanup()
    server.resetHandlers()
    const { router } = renderGraphRoute('/entities/entity%3A7')
    await bodyShown()
    server.use(...typedGraphOffHandlers)
    const before = asked().length
    await act(() => router.navigate('/entities/entity%3A10'))
    expect(await screen.findByText(ENTITY_NOT_ENABLED, {}, GRAPH_WAIT)).toBeInTheDocument()
    expect(h1(ENTITY_TITLE)).toBeInTheDocument()
    const after = askedAfter(before)
    expect(after.slice(0, 2)).toEqual([ENTITY_10, STATUS])
    expect(after.filter((url) => url !== ENTITY_10 && url !== STATUS)).toEqual([])
  }, 20_000)

  it('XR-7: a 429 offers a retry and a 503 is not sent again', async () => {
    const user = userEvent.setup()
    let pressed = false
    serveAt(ENTITY_7, () => (pressed ? undefined : HttpResponse.json({ detail: 't' }, { status: 429, headers: { 'Retry-After': '7' } })))
    renderGraphRoute('/entities/entity%3A7')
    const region = await screen.findByRole('region', { name: 'Entity entity:7' }, GRAPH_WAIT)
    await waitFor(() => expect(within(region).getByRole('status').textContent).toBe(THROTTLED_SEVEN), GRAPH_WAIT)
    const button = await screen.findByRole('button', { name: TRY_ENTITY_AGAIN }, GRAPH_WAIT)
    expect(screen.queryByRole('link', { name: EXPLORE_ENTITY })).toBeNull()
    await settled()
    expect([[STATUS, ENTITY_7], [STATUS, ENTITY_7, ENTITY_7]]).toContainEqual(asked())
    const before = asked().length
    pressed = true
    await user.click(button)
    await bodyShown()
    expect(askedAfter(before)).toEqual([ENTITY_7])
    expect(await screen.findByRole('link', { name: EXPLORE_ENTITY }, GRAPH_WAIT)).toBeInTheDocument()
    cleanup()
    server.resetHandlers()
    serveAt(ENTITY_7, () => HttpResponse.json({ detail: 'x' }, { status: 503 }))
    const second = asked().length
    renderGraphRoute('/entities/entity%3A7')
    expect(await screen.findByText(LATER, {}, GRAPH_WAIT)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: TRY_ENTITY_AGAIN })).toBeInTheDocument()
    await pause(1000)
    expect(askedAfter(second)).toEqual([STATUS, ENTITY_7])
  }, 20_000)

  it("XR-8: the page's releases or browser reach its requests and its Explore link", async () => {
    const user = userEvent.setup()
    const pinned = { ...tf1Body(), query: { ...tf1Body().query, releases: ['release:1'] } }
    server.use(http.get('/api/graph/v1/entities/:id', ({ request }) => {
      const url = new URL(request.url)
      return url.pathname === ENTITY_7 && url.search.startsWith('?releases=') ? HttpResponse.json(pinned as unknown as JsonBodyType) : undefined
    }))
    let before = asked().length
    renderGraphRoute('/entities/entity%3A7?releases=release%3A1')
    expect(await screen.findByRole('link', { name: EXPLORE_ENTITY }, GRAPH_WAIT)).toHaveAttribute('href', '/explore?v=1&seeds=entity%3A7&releases=release%3A1')
    expect(askedAfter(before)).toEqual([STATUS, '/api/graph/v1/entities/entity%3A7?releases=release:1'])
    cleanup()
    before = asked().length
    renderGraphRoute('/entities/entity%3A7?releases=release%3A2%2Crelease%3A1')
    expect(await screen.findByRole('link', { name: EXPLORE_ENTITY }, GRAPH_WAIT))
      .toHaveAttribute('href', '/explore?v=1&seeds=entity%3A7&releases=release%3A1%2Crelease%3A2')
    expect(askedAfter(before)).toEqual([STATUS, '/api/graph/v1/entities/entity%3A7?releases=release:1,release:2'])
    cleanup()
    const scoped = `${RELATIONSHIP_1}?browser=synthetic-regulation`
    const scopedEvidence = `${EVIDENCE_1}?browser=synthetic-regulation`
    const stubs: Record<string, string> = { [scoped]: 'relationship-encodes', [scopedEvidence]: 'evidence-encodes' }
    const answer = ({ request }: { request: Request }) => {
      const url = new URL(request.url)
      const name = stubs[url.pathname + url.search]
      return name === undefined ? undefined : HttpResponse.json(recorded<JsonBodyType>(name).body)
    }
    server.use(http.get('/api/graph/v1/relationships/:ref', answer), http.get('/api/graph/v1/relationships/:ref/evidence', answer))
    before = asked().length
    renderGraphRoute('/entities/entity%3A7?browser=synthetic-regulation')
    expect(await screen.findByRole('link', { name: EXPLORE_ENTITY }, GRAPH_WAIT)).toHaveAttribute('href', '/explore?v=1&seeds=entity%3A7&browser=synthetic-regulation')
    await bodyShown()
    await user.click(within(entityRegion(TF1)).getByRole('button', { name: ENCODES_ROW }))
    await askedFor(scopedEvidence)
    await answered(scopedEvidence)
    expect(askedAfter(before)).toEqual([STATUS, ENTITY_7_BROWSER, scoped, scopedEvidence])
  }, 20_000)

  const UNUSABLE: [string, string, string | null, string | null][] = [
    ['/entities/7', ENTITY_ADDRESS_PROBLEM, null, null],
    ['/entities/entity:0', ENTITY_ADDRESS_PROBLEM, null, null],
    ['/entities/ENTITY:7', ENTITY_ADDRESS_PROBLEM, null, null],
    ['/entities/entity%3A7?releases=release%3A1&browser=synthetic-regulation&utm_source=news', selectionProblemText('releases and browser'),
      selectionRemovalText('releases and browser'), '/entities/entity%3A7?utm_source=news'],
    ['/entities/entity%3A7?browser=a&browser=b', selectionProblemText('browser'), selectionRemovalText('browser'), '/entities/entity%3A7'],
  ]
  it.each(UNUSABLE)('XR-9: an address the page cannot use sends nothing (%s)', async (path, text, link, href) => {
    const user = userEvent.setup()
    renderGraphRoute(path)
    expect(await screen.findByText(text, {}, GRAPH_WAIT)).toBeInTheDocument()
    expect(h1(ENTITY_TITLE)).toBeInTheDocument()
    await pause(300)
    expect(asked()).toEqual([STATUS])
    if (link === null) {
      expect(hrefs()).toEqual([])
      return
    }
    const anchor = screen.getByRole('link', { name: link })
    expect(anchor).toHaveAttribute('href', href)
    if (path.includes('utm_source')) {
      // PT-3: the problem's h1 takes focus on the click and goes with the navigation it starts, so a focusin record
      // shows it; document.activeElement, read after the click, cannot.
      const problem = h1(ENTITY_TITLE)
      const focus = recordFocus()
      try {
        await user.click(anchor)
        // The click focuses the link, then its handler the h1, before the navigation unmounts both.
        expect(focus.focused[0]).toBe(anchor)
        expect(focus.focused[1]).toBe(problem)
        expect(problem.isConnected).toBe(false)
        await askedFor(ENTITY_7)
        const heading = await screen.findByRole('heading', { level: 1, name: TF1 }, GRAPH_WAIT)
        await waitFor(() => expect(document.activeElement).toBe(heading), GRAPH_WAIT)
        await bodyShown()
      } finally {
        focus.stop()
      }
    }
  }, 20_000)

  it.each(['reference_relationships', 'releases'])('XR-10: a body whose lists hold a null shows no route error, the page standing with F3\'s bad-body words in its panel (%s)', async (list) => {
    const body = copy(tf1Body()) as unknown as Record<string, unknown[]>
    body[list] = [null, ...body[list]]
    serveAt(ENTITY_7, () => HttpResponse.json(body as unknown as JsonBodyType))
    renderGraphRoute('/entities/entity%3A7')
    await waitFor(() => expect(within(entityRegion(TF1)).getByText(BAD_BODY)).toBeInTheDocument(), GRAPH_WAIT)
    expect(screen.getAllByRole('heading', { level: 1 }).map((node) => node.textContent)).toEqual([TF1])
    expect(screen.queryByText(PAGE_UNKNOWN)).toBeNull()
    expect(screen.queryByRole('button', { name: RELOAD })).toBeNull()
    const badge = page().querySelector('p') as HTMLElement
    expect(badge).toHaveAttribute('aria-hidden', 'true')
    expect(badge.textContent).toBe(SYNTHETIC_BADGE)
    expect(screen.getByRole('link', { name: EXPLORE_ENTITY })).toBeInTheDocument()
    expect(asked()).toEqual([STATUS, ENTITY_7])
    // The allowance (12.0; F3's takeCaughtLog): React's one report of the error the boundary caught, then cleared.
    const errors = vi.mocked(console.error).mock.calls.map((args) => String(args[0]))
    expect(errors).toEqual(['%o\n\n%s\n\n%s\n'])
    expect(vi.mocked(console.warn).mock.calls).toEqual([])
    vi.mocked(console.error).mockClear()
  }, 20_000)

  it('XR-11: labels and release titles that hold addresses stay text on the entity page, and every address in it is a router path or an http(s) one', async () => {
    const user = userEvent.setup()
    const hostileRelease = <T extends { dataset: { title: string }; version: string; licence: { url: string | null } }>(release: T): T => ({
      ...release, dataset: { ...release.dataset, title: '//evil.example/x' }, version: 'javascript:alert(1)', licence: { ...release.licence, url: 'javascript:alert(1)' },
    })
    const entity = copy(tf1Body())
    entity.entity.label = 'javascript:alert(1)'
    entity.nodes[0].label = 'javascript:alert(1)'
    entity.nodes[1].label = '//evil.example/x'
    entity.identifiers[0].url = 'javascript:alert(1)'
    entity.releases = entity.releases.map(hostileRelease)
    const relationship = copy(recorded<GraphRelationshipResponse>('relationship-encodes').body)
    relationship.nodes[0].label = 'javascript:alert(1)'
    relationship.nodes[1].label = '//evil.example/x'
    relationship.releases = relationship.releases.map(hostileRelease)
    serveAt(ENTITY_7, () => HttpResponse.json(entity as unknown as JsonBodyType))
    serveAt(RELATIONSHIP_1, () => HttpResponse.json(relationship as unknown as JsonBodyType), '/api/graph/v1/relationships/:ref')
    renderGraphRoute('/entities/entity%3A7')
    await bodyShown()
    expect(screen.getAllByRole('heading', { level: 1 }).map((node) => node.textContent)).toEqual(['javascript:alert(1) (Gene)'])
    const name = 'Encodes: javascript:alert(1) (Gene) to //evil.example/x (Protein (UniProtKB entry))'
    await user.click(screen.getByRole('button', { name }))
    expect(await screen.findByRole('region', { name }, GRAPH_WAIT)).toBeInTheDocument()
    await answered(RELATIONSHIP_1)
    await answered(EVIDENCE_1)
    expect(screen.getByRole('link', { name: EXPLORE_ENTITY })).toHaveAttribute('href', '/explore?v=1&seeds=entity%3A7')
    expect(Array.from(page().querySelectorAll('a'), (link) => link.textContent ?? '').filter((text) => text.includes('javascript:'))).toEqual([])
    expect(strayAddresses()).toEqual([])
    expect(hrefs().filter((href) => href.includes('javascript:') || href.includes('evil.example'))).toEqual([])
    expect(asked()).toEqual([STATUS, ENTITY_7, RELATIONSHIP_1, EVIDENCE_1])
  }, 20_000)
})

describe('the entity lookup', () => {
  it('XL-1: one resolved result replaces the lookup with the entity, and Back skips it', async () => {
    const resolve = held()
    server.use(http.get('/api/graph/v1/resolve', async () => {
      await resolve.promise
      return undefined
    }))
    const { router } = renderGraphRoute('/')
    await act(() => router.navigate('/entities/synthetic_gene/SYN-TF1'))
    // The lazy fallback's h1 has the same name, so the page's own is the one its subject line follows.
    await waitFor(() => expect(h1(ENTITY_LOOKUP_TITLE).nextElementSibling?.textContent).toBe(lookupSubjectText(GENE_ID, 'synthetic_gene', 'SYN-TF1')), GRAPH_WAIT)
    const heading = h1(ENTITY_LOOKUP_TITLE)
    await waitFor(() => expect(lookupLine().textContent).toBe(LOOKUP_SEARCHING), GRAPH_WAIT)
    expect(document.activeElement).not.toBe(heading)
    resolve.release()
    await waitFor(() => expect(router.state.location.pathname).toBe('/entities/entity%3A7'), GRAPH_WAIT)
    expect(router.state.location.search).toBe('')
    expect(router.state.historyAction).toBe('REPLACE')
    const entity = await screen.findByRole('heading', { level: 1, name: TF1 }, GRAPH_WAIT)
    await waitFor(() => expect(document.activeElement).toBe(entity), GRAPH_WAIT)
    await bodyShown()
    expect(asked()).toEqual([STATUS, RESOLVE_NS_TF1, ENTITY_7])
    await act(() => router.navigate(-1))
    expect(router.state.location.pathname).toBe('/')
  }, 20_000)

  it("XL-2: an isoform's identifier redirects to its page", async () => {
    const { router } = renderGraphRoute('/entities/synthetic_protein/SYN-P1-1')
    expect(await screen.findByRole('heading', { level: 1, name: 'SYN-P1-1 (Protein isoform)' }, GRAPH_WAIT)).toBeInTheDocument()
    expect(router.state.location.pathname).toBe('/entities/entity%3A11')
    expect(router.state.historyAction).toBe('REPLACE')
    await answered(ENTITY_11)
    expect(asked()).toEqual([STATUS, RESOLVE_NS_ISOFORM, ENTITY_11])
  }, 20_000)

  it('XL-3: the redirect carries the releases and leaves unknown names behind', async () => {
    const resolve = '/api/graph/v1/resolve?q=SYN-TF1&namespace=synthetic_gene&releases=release:1'
    const entity = '/api/graph/v1/entities/entity%3A7?releases=release:1'
    serveResolve(resolve, recorded<JsonBodyType>('resolve-ns-tf1').body)
    server.use(http.get('/api/graph/v1/entities/:id', ({ request }) => {
      const url = new URL(request.url)
      return url.pathname + url.search === entity ? HttpResponse.json(tf1Body() as unknown as JsonBodyType) : undefined
    }))
    const { router } = renderGraphRoute('/entities/synthetic_gene/SYN-TF1?releases=release%3A1&utm_source=news')
    await waitFor(() => expect(router.state.location.pathname).toBe('/entities/entity%3A7'), GRAPH_WAIT)
    expect(router.state.location.search).toBe('?releases=release%3A1')
    await answered(entity)
    await bodyShown()
    expect(asked()).toEqual([STATUS, resolve, entity])
  }, 20_000)

  it('XL-4: a case candidate is offered as a link under the badge and never followed', async () => {
    const user = userEvent.setup()
    const { router } = renderGraphRoute('/entities/synthetic_gene/syn-tf1')
    await waitFor(() => expect(lookupLine().textContent).toBe(lookupCandidateText('syn-tf1')), GRAPH_WAIT)
    expect(asked()).toEqual([STATUS, RESOLVE_NS_CASE])
    expect(router.state.location.pathname).toBe('/entities/synthetic_gene/syn-tf1')
    expect(router.state.historyAction).toBe('POP')
    const subject = screen.getByText(lookupSubjectText(GENE_ID, 'synthetic_gene', 'syn-tf1'))
    expect(badges()).toHaveLength(1)
    expect(subject.nextElementSibling).toBe(badges()[0])
    expect(badges()[0].firstElementChild?.textContent).toBe(SYNTHETIC_BADGE)
    expect(badges()[0]).not.toHaveAttribute('aria-hidden')
    const items = candidates()
    expect(items).toHaveLength(1)
    const link = within(items[0]).getByRole('link', { name: openText(TF1) })
    expect(link).toHaveAttribute('href', '/entities/entity%3A7')
    expect(items[0].querySelector('p')?.textContent).toBe(FACTS_CASE)
    expect(document.activeElement).toBe(document.body)
    await user.click(link)
    expect(router.state.historyAction).toBe('PUSH')
    expect(router.state.location.pathname).toBe('/entities/entity%3A7')
    await askedFor(ENTITY_7)
    const heading = await screen.findByRole('heading', { level: 1, name: TF1 }, GRAPH_WAIT)
    await waitFor(() => expect(document.activeElement).toBe(heading), GRAPH_WAIT)
    await bodyShown()
    const before = asked().length
    await act(() => router.navigate(-1))
    expect(await screen.findByRole('list', { name: LOOKUP_CANDIDATES_NAME }, GRAPH_WAIT)).toBeInTheDocument()
    expect(askedAfter(before)).toEqual([])
  }, 20_000)

  it('XL-5: under a namespace no label is searched', async () => {
    const { router } = renderGraphRoute('/entities/synthetic_gene/SYN-ALPHA')
    await waitFor(() => expect(lookupLine().textContent).toBe(lookupUnmatchedText('SYN-ALPHA', GENE_ID)), GRAPH_WAIT)
    expect(asked()).toEqual([STATUS, RESOLVE_NS_ALPHA])
    expect(screen.queryByRole('list', { name: LOOKUP_CANDIDATES_NAME })).toBeNull()
    expect(router.state.location.pathname).toBe('/entities/synthetic_gene/SYN-ALPHA')
    expect(badges()).toEqual([])
  }, 20_000)

  it("XL-6: the API's 400 is worded from its parameters, with a removal link for a selection", async () => {
    const user = userEvent.setup()
    const detail = recorded<{ detail: string }>('resolve-ns-unknown').body.detail
    renderGraphRoute('/entities/no_such_namespace/SYN-TF1')
    await waitFor(() => expect(lookupLine().textContent).toBe(NAMESPACE_PROBLEM), GRAPH_WAIT)
    expect(asked()).toEqual([STATUS, RESOLVE_NS_UNKNOWN])
    expect(screen.getByText(lookupSubjectText(null, 'no_such_namespace', 'SYN-TF1'))).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: TRY_AGAIN })).toBeNull()
    await pause(300)
    expect(asked()).toEqual([STATUS, RESOLVE_NS_UNKNOWN])
    expect(page().textContent ?? '').not.toContain(detail)
    cleanup()
    const pinned = '/api/graph/v1/resolve?q=SYN-TF1&namespace=synthetic_gene&releases=release:9'
    serveResolve(pinned, () => HttpResponse.json({ detail: 'x', errors: [{ parameter: 'releases', value: 'release:9', allowed: null, limit: null }] }, { status: 400 }))
    const { router } = renderGraphRoute('/entities/synthetic_gene/SYN-TF1?releases=release%3A9')
    await waitFor(() => expect(lookupLine().textContent).toBe(selectionProblemText('releases')), GRAPH_WAIT)
    const link = screen.getByRole('link', { name: selectionRemovalText('releases') })
    expect(link).toHaveAttribute('href', '/entities/synthetic_gene/SYN-TF1')
    const before = asked().length
    // PT-3: the lookup's h1 takes focus on the click and goes with the body the new address keys afresh.
    const lookupHeading = h1(ENTITY_LOOKUP_TITLE)
    const focus = recordFocus()
    try {
      await user.click(link)
      // The click focuses the link, then its handler the h1, before the new address's body replaces both.
      expect(focus.focused[0]).toBe(link)
      expect(focus.focused[1]).toBe(lookupHeading)
      expect(lookupHeading.isConnected).toBe(false)
    } finally {
      focus.stop()
    }
    await waitFor(() => expect(router.state.location.pathname).toBe('/entities/entity%3A7'), GRAPH_WAIT)
    expect(askedAfter(before)[0]).toBe(RESOLVE_NS_TF1)
    await bodyShown()
  }, 20_000)

  it('XL-7: alias candidates are listed in the API\'s order and never followed', async () => {
    const alias = '/api/graph/v1/resolve?q=SYN-N944-ALIAS&namespace=synthetic_gene'
    const shared = '/api/graph/v1/resolve?q=SYN-N-SHARED-ALIAS&namespace=synthetic_gene'
    serveResolve(alias, namespaceAliasResolve as unknown as JsonBodyType)
    serveResolve(shared, namespaceAmbiguousResolve as unknown as JsonBodyType)
    let view = renderGraphRoute('/entities/synthetic_gene/SYN-N944-ALIAS')
    await waitFor(() => expect(lookupLine().textContent).toBe(lookupCandidateText('SYN-N944-ALIAS')), GRAPH_WAIT)
    const one = candidates()
    expect(one.map((entry) => within(entry).getByRole('link').textContent)).toEqual([openText('SYN-N944 (Gene)')])
    expect(within(one[0]).getByRole('link')).toHaveAttribute('href', '/entities/entity%3A944')
    expect(one[0].querySelector('p')?.textContent).toBe(FACTS_ALIAS)
    expect(view.router.state.location.pathname).toBe('/entities/synthetic_gene/SYN-N944-ALIAS')
    expect(screen.getByText(lookupSubjectText(GENE_ID, 'synthetic_gene', 'SYN-N944-ALIAS')).nextElementSibling).toBe(badges()[0])
    cleanup()
    view = renderGraphRoute('/entities/synthetic_gene/SYN-N-SHARED-ALIAS')
    await waitFor(() => expect(lookupLine().textContent).toBe(lookupAmbiguousText('SYN-N-SHARED-ALIAS')), GRAPH_WAIT)
    const two = candidates()
    expect(two.map((entry) => within(entry).getByRole('link').textContent)).toEqual([openText('SYN-N945 (Gene)'), openText('SYN-N946 (Gene)')])
    for (const entry of two) expect(entry.querySelector('p')?.textContent).toBe(FACTS_ALIAS)
    expect(view.router.state.location.pathname).toBe('/entities/synthetic_gene/SYN-N-SHARED-ALIAS')
    expect(screen.getByText(lookupSubjectText(GENE_ID, 'synthetic_gene', 'SYN-N-SHARED-ALIAS')).nextElementSibling).toBe(badges()[0])
  }, 20_000)

  const REFUSED: [string, string | null][] = [
    ['/entities/synthetic_gene/a%2Cb', separatorValueText('a,b')],
    ['/entities/synthetic_gene/a%09b', separatorValueText('a\tb')],
    ['/entities/synthetic_gene/a%07b', controlValueText('a\u0007b')],
    ['/entities/synthetic_gene/%20%20', EMPTY_VALUE],
    [`/entities/synthetic_gene/${'a'.repeat(201)}`, longValueText('a'.repeat(201), 200)],
    ['/entities/synthetic_gene/a%2Fb', null],
  ]
  it.each(REFUSED)('XL-8: a value the API would refuse is never sent (%s)', async (path, line) => {
    if (line === null) serveResolve('/api/graph/v1/resolve?q=a%2Fb&namespace=synthetic_gene', makeResolveResponse(['a/b']) as unknown as JsonBodyType)
    const { router } = renderGraphRoute(path)
    if (line === null) {
      await waitFor(() => expect(lookupLine().textContent).toBe(lookupUnmatchedText('a/b', GENE_ID)), GRAPH_WAIT)
      expect(screen.getByText(lookupSubjectText(GENE_ID, 'synthetic_gene', 'a/b'))).toBeInTheDocument()
      expect(asked()).toEqual([STATUS, '/api/graph/v1/resolve?q=a%2Fb&namespace=synthetic_gene'])
      return
    }
    await waitFor(() => expect(lookupLine().textContent).toBe(line), GRAPH_WAIT)
    await pause(300)
    expect(asked()).toEqual([STATUS])
    expect(router.state.location.pathname).toBe(path)
  }, 20_000)

  it('XL-9: with entity pages off the redirect reaches the off sentence', async () => {
    statusWith((body) => ({ ...body, features: { ...body.features, entities: false } }))
    const { router } = renderGraphRoute('/entities/synthetic_gene/SYN-TF1')
    expect(await screen.findByText(ENTITY_NOT_ENABLED, {}, GRAPH_WAIT)).toBeInTheDocument()
    expect(h1(ENTITY_TITLE)).toBeInTheDocument()
    expect(router.state.location.pathname).toBe('/entities/entity%3A7')
    expect(asked()).toEqual([STATUS, RESOLVE_NS_TF1])
  }, 20_000)

  it('XL-10: a failed lookup offers a retry that keeps focus', async () => {
    const user = userEvent.setup()
    const retry = held()
    let calls = 0
    server.use(http.get('/api/graph/v1/resolve', async ({ request }) => {
      const url = new URL(request.url)
      if (url.pathname + url.search !== RESOLVE_NS_TF1) return undefined
      calls += 1
      if (calls === 1) return HttpResponse.json({ detail: 'x' }, { status: 500 })
      await retry.promise
      return HttpResponse.json(recorded<GraphResolveResponse>('resolve-ns-tf1').body as unknown as JsonBodyType)
    }))
    const { router } = renderGraphRoute('/entities/synthetic_gene/SYN-TF1')
    await waitFor(() => expect(lookupLine().textContent).toBe(LOOKUP_ERROR), GRAPH_WAIT)
    const button = screen.getByRole('button', { name: TRY_AGAIN })
    await user.click(button)
    await waitFor(() => expect(button).toHaveAttribute('aria-disabled', 'true'), GRAPH_WAIT)
    expect(button).toBeEnabled()
    expect(document.activeElement).toBe(button)
    retry.release()
    await waitFor(() => expect(router.state.location.pathname).toBe('/entities/entity%3A7'), GRAPH_WAIT)
    await bodyShown()
    expect(asked()).toEqual([STATUS, RESOLVE_NS_TF1, RESOLVE_NS_TF1, ENTITY_7])
    cleanup()
    server.resetHandlers()
    serveResolve(RESOLVE_NS_TF1, () => HttpResponse.json({ detail: 't' }, { status: 429, headers: { 'Retry-After': '7' } }))
    renderGraphRoute('/entities/synthetic_gene/SYN-TF1')
    await waitFor(() => expect(lookupLine().textContent).toBe(THROTTLED_SEVEN), GRAPH_WAIT)
    expect(screen.getByRole('button', { name: TRY_AGAIN })).toBeInTheDocument()
  }, 20_000)

  it('XL-11: a 404 from a switched-off portal rechecks status once', async () => {
    const { router } = renderGraphRoute('/entities/synthetic_gene/syn-tf1')
    await screen.findByRole('list', { name: LOOKUP_CANDIDATES_NAME }, GRAPH_WAIT)
    server.use(...typedGraphOffHandlers)
    const before = asked().length
    await act(() => router.navigate('/entities/synthetic_gene/SYN-ALPHA'))
    expect(await screen.findByText(ENTITY_LOOKUP_NOT_ENABLED, {}, GRAPH_WAIT)).toBeInTheDocument()
    expect(h1(ENTITY_LOOKUP_TITLE)).toBeInTheDocument()
    await answered(STATUS)
    expect(askedAfter(before)).toEqual([RESOLVE_NS_ALPHA, STATUS])
  }, 20_000)

  it('XL-12: candidate labels that hold addresses stay text, the links open entity pages, and a candidate outside the ref grammar reaches no link', async () => {
    const shared = '/api/graph/v1/resolve?q=SYN-N-SHARED-ALIAS&namespace=synthetic_gene'
    const body = copy(namespaceAmbiguousResolve)
    body.results[0].candidates[0].entity.label = 'javascript:alert(1)'
    body.results[0].candidates[1].entity.label = '//evil.example/x'
    body.releases[0].dataset.title = '//evil.example/x'
    serveResolve(shared, body as unknown as JsonBodyType)
    renderGraphRoute('/entities/synthetic_gene/SYN-N-SHARED-ALIAS')
    await waitFor(() => expect(lookupLine().textContent).toBe(lookupAmbiguousText('SYN-N-SHARED-ALIAS')), GRAPH_WAIT)
    expect(candidates().map((entry) => within(entry).getByRole('link').textContent))
      .toEqual([openText('javascript:alert(1) (Gene)'), openText('//evil.example/x (Gene)')])
    expect(hrefs()).toEqual(['/entities/entity%3A945', '/entities/entity%3A946'])
    expect(strayAddresses()).toEqual([])
    expect(asked()).toEqual([STATUS, shared])
    cleanup()
    server.resetHandlers()
    const outside = copy(body)
    outside.results[0].candidates[1].entity.id = '//evil.example/x'
    serveResolve(shared, outside as unknown as JsonBodyType)
    renderGraphRoute('/entities/synthetic_gene/SYN-N-SHARED-ALIAS')
    await waitFor(() => expect(lookupLine().textContent).toBe(LOOKUP_ERROR), GRAPH_WAIT)
    expect(screen.getByRole('button', { name: TRY_AGAIN })).toBeInTheDocument()
    expect(screen.queryByRole('list', { name: LOOKUP_CANDIDATES_NAME })).toBeNull()
    expect(hrefs()).toEqual([])
  }, 20_000)
})
