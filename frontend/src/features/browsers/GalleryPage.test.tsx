import { act, cleanup, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import type { JsonBodyType } from 'msw'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import type { MockInstance } from 'vitest'
import {
  citationCardInstance, externalInstance, hostile, hostileInstance, makeBrowsersResponse, plannedInstance, plannedPlainInstance,
  plannedPortalInstance, retiredInstance, retiredPortalInstance, unavailableInstance, unlistedInstance,
} from '../../mocks/fixtures/typedGraph/extras'
import { typedGraphVisitorHandlers } from '../../mocks/handlers/typedGraph'
import { server } from '../../mocks/server'
import { useAuthStore } from '../../store/authStore'
import { GRAPH_WAIT, recordRequests, recorded } from '../../test/graphHooks'
import type { RequestLog } from '../../test/graphHooks'
import { recordAnswers, renderGraphRoute } from '../../test/graphRoutes'
import type { GraphBrowsersResponse, GraphInstance } from '../graph/types'
import { isolateText } from '../graph/words/isolateText'

let requests: RequestLog
let answers: { answers: string[]; stop: () => void }
let scrolls: MockInstance

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
  // jsdom prints "Not implemented" for a call no spy replaces, which no console spy sees.
  scrolls = vi.spyOn(window, 'scrollTo').mockImplementation(() => {})
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

// Words copied from the spec's sections 3.1 to 3.4 and 3.6, with 6.1's suffix and 6.2's taxon words, never from the modules under test.
const TITLE = 'Browsers'
const NOT_ENABLED = 'Browsers are not enabled on this portal.'
const PAGE_UNKNOWN = 'This page could not be loaded.'
const TRY_AGAIN = 'Try again'
const SERVER_UNREACHABLE = 'The server could not be reached.'
const THROTTLED_SEVEN = 'Too many requests. Try again in 7 seconds.'
const SEARCH_FORM_NAME = 'Search the browsers'
const SEARCH_LABEL = 'Search titles and summaries'
const SEARCH_BUTTON = 'Search'
const SEARCH_CLEAR = 'Clear search'
const FILTERS_NAME = 'Filters'
const FILTER_LABELS = {
  tag: 'Biology area', relationship_type: 'Relationship type', taxon: 'Organism', assay: 'Assay', year: 'Publication year',
  status: 'Stated status', availability: 'Data availability',
}
const ANY_OPTION = {
  tag: 'Any biology area', relationship_type: 'Any relationship type', taxon: 'Any organism', assay: 'Any assay', year: 'Any year',
  status: 'Any stated status', availability: 'Any availability',
}
const CLEAR_FILTERS = 'Clear filters'
const LOADING_BROWSERS = 'Loading browsers…'
const ONE_LISTED = '1 browser listed.'
const TWO_LISTED = '2 browsers listed.'
const NINE_LISTED = '9 browsers listed.'
const EMPTY_LIST = 'No browser is listed here for you.'
const NO_FILTER_MATCH = 'No browser matches these filters.'
const NO_BOTH_MATCH = 'No browser matches this search and these filters.'
const INCOMPLETE_LIST = 'This list may be incomplete: the server sent only its first page.'
const LIST_ERROR = 'The browsers could not be loaded.'
const WHOLE_ADDRESS = 'The gallery cannot use this address.'
const SEARCH_ERROR = 'The gallery cannot use the search in this address.'
const TAG_ERROR = 'The gallery cannot use the biology area in this address.'
const TAXON_ERROR = 'The gallery cannot use the organism in this address.'
const YEAR_ERROR = 'The gallery cannot use the publication year in this address.'
const SEARCH_YEAR_ERROR = 'The gallery cannot use the search and publication year in this address.'
const WHOLE_GALLERY = 'Show the whole gallery'
const WITHOUT_SEARCH = 'Show the gallery without the search'
const WITHOUT_TAG = 'Show the gallery without the biology area'
const WITHOUT_TAXON = 'Show the gallery without the organism'
const WITHOUT_YEAR = 'Show the gallery without the publication year'
const WITHOUT_SEARCH_YEAR = 'Show the gallery without the search and publication year'
const GENE_REGULATION = 'Gene regulation'
const PROTEIN_INTERACTIONS = 'Protein interactions'
const AVAILABLE = 'Available'
const PLANNED = 'Planned'
const LIVE = 'Live'
const STAFF_ONLY = 'Staff only'
const UNLISTED = 'Unlisted'
const SYNTHETIC_BADGE = 'Synthetic demonstration data'
const EXTERNAL_ACTION = 'External portal at'
const SYNTHETIC_SUFFIX = ' (synthetic)'
const SYNTHETIC_TAXON = 'synthetic construct (taxid 32630)'
// The recorded vocabularies' type labels, the recorded titles, and an assay value shown as the address gives it.
const PHYSICAL = 'Physical interaction'
const REGULATION = 'Transcriptional regulation'
const TYPED_TITLE = 'Synthetic regulation example'
const PORTAL_TITLE = 'Protein interactions on this portal'
const ASSAY = 'MI:0018'

const STATUS = '/api/graph/v1/status'
const BROWSERS = '/api/graph/v1/browsers'
const INSTANCE = '/api/graph/v1/browsers/synthetic-regulation'
const asked = () => requests.urls.map((url) => url.pathname + url.search)
const page = () => document.querySelector('main') as HTMLElement
const select = (label: string) => screen.getByRole('combobox', { name: label }) as HTMLSelectElement
const shownOption = (label: string) => select(label).selectedOptions[0]?.textContent ?? null
const optionTexts = (label: string) => Array.from(select(label).options, (option) => option.textContent)
const cards = () => screen.queryAllByRole('heading', { level: 2 }).map((node) => node.textContent)
const countLine = () => screen.getByRole('status')
/** 12.0's address check: every address inside the page is http(s), the scheme in any case, or a single-slash router path. */
const strayAddresses = () => Array.from(page().querySelectorAll('a[href]'), (link) => link.getAttribute('href') ?? '')
  .filter((href) => !/^(?:https?:\/\/|\/(?!\/))/i.test(href))
const galleryBody = () => recorded<GraphBrowsersResponse>('browsers').body
const serveEvery = (body: JsonBodyType) => server.use(http.get(BROWSERS, () => HttpResponse.json(body)))
/** The options come from the unfiltered list, which may answer after the filtered one. */
const optionsLoaded = () => waitFor(() => expect(optionTexts(FILTER_LABELS.tag)).toContain(GENE_REGULATION), GRAPH_WAIT)

function held() {
  let release: () => void = () => {}
  const promise = new Promise<void>((resolve) => {
    release = resolve
  })
  return { promise, release: () => release() }
}

describe('the gallery', () => {
  it('GP-1: the heading, the search landmark, seven named filters with their options, the count and the cards in order', async () => {
    renderGraphRoute('/browsers')
    expect(await screen.findByText(TWO_LISTED, {}, GRAPH_WAIT)).toBeInTheDocument()
    expect(screen.getAllByRole('heading', { level: 1 }).map((node) => node.textContent)).toEqual([TITLE])
    const landmark = screen.getByRole('search', { name: SEARCH_FORM_NAME })
    const box = within(landmark).getByRole('searchbox', { name: SEARCH_LABEL }) as HTMLInputElement
    expect(box.maxLength).toBe(200)
    expect(within(landmark).getAllByRole('button').map((node) => node.textContent)).toEqual([SEARCH_BUTTON])
    const group = screen.getByRole('group', { name: FILTERS_NAME })
    const boxes = within(group).getAllByRole('combobox')
    expect(boxes).toHaveLength(7)
    const expected: [keyof typeof FILTER_LABELS, string[]][] = [
      ['tag', [GENE_REGULATION, PROTEIN_INTERACTIONS]],
      ['relationship_type', [PHYSICAL, REGULATION]],
      ['taxon', [SYNTHETIC_TAXON]],
      ['assay', []],
      ['year', ['2026']],
      ['status', [AVAILABLE]],
      ['availability', [LIVE]],
    ]
    expected.forEach(([name, options], index) => {
      expect(within(group).getByRole('combobox', { name: FILTER_LABELS[name] })).toBe(boxes[index])
      expect(Array.from((boxes[index] as HTMLSelectElement).options, (option) => option.textContent), name).toEqual([ANY_OPTION[name], ...options])
    })
    expect(countLine().textContent).toBe(TWO_LISTED)
    const lists = screen.getAllByRole('list')
    expect(lists).toHaveLength(1)
    expect(within(lists[0]).getAllByRole('listitem')).toHaveLength(2)
    const headings = screen.getAllByRole('heading', { level: 2 })
    expect(headings.map((node) => node.textContent)).toEqual([PORTAL_TITLE, TYPED_TITLE])
    for (const heading of headings) expect(within(heading).getByRole('link')).toBeInTheDocument()
    expect(asked()).toEqual([STATUS, BROWSERS])
  }, 20_000)

  it('GP-2: a search is sent on submit only, pushes, follows Back and Forward, and Clear search focuses the box', async () => {
    const user = userEvent.setup()
    const { router } = renderGraphRoute('/browsers')
    await screen.findByText(TWO_LISTED, {}, GRAPH_WAIT)
    const box = screen.getByRole('searchbox', { name: SEARCH_LABEL }) as HTMLInputElement
    let n = asked().length
    await user.type(box, 'search')
    expect(asked().slice(n)).toEqual([])
    await user.keyboard('{Enter}')
    await screen.findByText(ONE_LISTED, {}, GRAPH_WAIT)
    expect(router.state.location.search).toBe('?q=search')
    expect(router.state.historyAction).toBe('PUSH')
    expect(asked().slice(n)).toEqual(['/api/graph/v1/browsers?q=search'])
    expect(cards()).toEqual([PORTAL_TITLE])
    expect(countLine().textContent).toBe(ONE_LISTED)
    // The same search submitted again writes nothing, so Back needs no extra press.
    const key = router.state.location.key
    await user.keyboard('{Enter}')
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 300))
    })
    expect(router.state.location.key).toBe(key)
    n = asked().length
    await user.clear(box)
    await user.type(box, 'synthetic{Enter}')
    await waitFor(() => expect(cards()).toEqual([TYPED_TITLE]), GRAPH_WAIT)
    expect(asked().slice(n)).toEqual(['/api/graph/v1/browsers?q=synthetic'])
    n = asked().length
    await act(() => router.navigate(-2))
    await screen.findByText(TWO_LISTED, {}, GRAPH_WAIT)
    expect(box.value).toBe('')
    expect(cards()).toHaveLength(2)
    expect(asked().slice(n)).toEqual([])
    await act(() => router.navigate(1))
    await screen.findByText(ONE_LISTED, {}, GRAPH_WAIT)
    expect(box.value).toBe('search')
    await user.click(screen.getByRole('button', { name: SEARCH_CLEAR }))
    await screen.findByText(TWO_LISTED, {}, GRAPH_WAIT)
    expect(document.activeElement).toBe(box)
    expect(router.state.location.search).toBe('')
    expect(router.state.historyAction).toBe('PUSH')
    // An emptied box, submitted, drops the search.
    await user.type(box, 'search{Enter}')
    await screen.findByText(ONE_LISTED, {}, GRAPH_WAIT)
    await user.clear(box)
    await user.keyboard('{Enter}')
    await screen.findByText(TWO_LISTED, {}, GRAPH_WAIT)
    expect(router.state.location.search).toBe('')
    expect(router.state.historyAction).toBe('PUSH')
    expect(asked()).toEqual([STATUS, BROWSERS, '/api/graph/v1/browsers?q=search', '/api/graph/v1/browsers?q=synthetic'])
  }, 20_000)

  it('GP-3: two selects by user event, one history entry for two changes of one select, the old cards kept while one loads', async () => {
    const user = userEvent.setup()
    // The first filtered answer is held, so the row can read the list area while it loads.
    const filtered = held()
    server.use(http.get(BROWSERS, async ({ request }) => {
      if (new URL(request.url).search === '') return undefined
      await filtered.promise
      return undefined
    }))
    const { router } = renderGraphRoute('/browsers')
    await screen.findByText(TWO_LISTED, {}, GRAPH_WAIT)
    const tag = select(FILTER_LABELS.tag)
    let n = asked().length
    await user.selectOptions(tag, GENE_REGULATION)
    await waitFor(() => expect(asked().slice(n)).toEqual(['/api/graph/v1/browsers?tag=gene_regulation']), GRAPH_WAIT)
    expect(countLine().textContent).toBe('')
    expect(screen.getByText(LOADING_BROWSERS)).toBeInTheDocument()
    expect(cards()).toEqual([PORTAL_TITLE, TYPED_TITLE])
    expect(screen.getByRole('list')).toHaveAttribute('aria-busy', 'true')
    filtered.release()
    await screen.findByText(ONE_LISTED, {}, GRAPH_WAIT)
    expect(screen.queryByText(LOADING_BROWSERS)).toBeNull()
    expect(screen.getByRole('list')).not.toHaveAttribute('aria-busy')
    expect(router.state.location.search).toBe('?tag=gene_regulation')
    expect(router.state.historyAction).toBe('PUSH')
    expect(asked().slice(n)).toEqual(['/api/graph/v1/browsers?tag=gene_regulation'])
    expect(cards()).toEqual([TYPED_TITLE])
    expect(document.activeElement).toBe(tag)
    n = asked().length
    await user.selectOptions(tag, ANY_OPTION.tag)
    await screen.findByText(TWO_LISTED, {}, GRAPH_WAIT)
    expect(router.state.historyAction).toBe('REPLACE')
    expect(router.state.location.pathname + router.state.location.search).toBe('/browsers')
    expect(cards()).toHaveLength(2)
    expect(asked().slice(n)).toEqual([])
    // Back returns to the address before the first change: the two changes of one select left one entry.
    await act(() => router.navigate(-1))
    expect(router.state.location.search).toBe('')
    expect(router.state.location.search).not.toBe('?tag=gene_regulation')
    await screen.findByText(TWO_LISTED, {}, GRAPH_WAIT)
    const year = select(FILTER_LABELS.year)
    n = asked().length
    await user.selectOptions(year, '2026')
    await screen.findByText(ONE_LISTED, {}, GRAPH_WAIT)
    expect(router.state.location.search).toBe('?year=2026')
    expect(router.state.historyAction).toBe('PUSH')
    expect(asked().slice(n)).toEqual(['/api/graph/v1/browsers?year=2026'])
    expect(cards()).toEqual([TYPED_TITLE])
    expect(document.activeElement).toBe(year)
    expect(asked()).toEqual([STATUS, BROWSERS, '/api/graph/v1/browsers?tag=gene_regulation', '/api/graph/v1/browsers?year=2026'])
  }, 20_000)

  it.each([
    ['?relationship_type=transcriptional_regulation', FILTER_LABELS.relationship_type, REGULATION, [TYPED_TITLE], ONE_LISTED],
    ['?taxon=32630', FILTER_LABELS.taxon, SYNTHETIC_TAXON, [TYPED_TITLE], ONE_LISTED],
    ['?assay=MI:0018', FILTER_LABELS.assay, ASSAY, [], NO_FILTER_MATCH],
    ['?status=available', FILTER_LABELS.status, AVAILABLE, [PORTAL_TITLE, TYPED_TITLE], TWO_LISTED],
    ['?availability=live', FILTER_LABELS.availability, LIVE, [PORTAL_TITLE, TYPED_TITLE], TWO_LISTED],
  ])('GP-3: the deep link %s shows its option, sends its request and lists its cards', async (search, label, option, expected, line) => {
    renderGraphRoute(`/browsers${search}`)
    expect(await screen.findByText(line, {}, GRAPH_WAIT)).toBeInTheDocument()
    await optionsLoaded()
    expect(shownOption(label)).toBe(option)
    expect(asked().sort()).toEqual([BROWSERS, `${BROWSERS}${search}`, STATUS])
    expect(cards()).toEqual(expected)
  }, 20_000)

  it('GP-4: deep links that match nothing show the value, the request, the no-match line and Clear filters', async () => {
    const cases: [string, string, string][] = [
      ['?assay=MI:0018', FILTER_LABELS.assay, ASSAY],
      ['?status=planned', FILTER_LABELS.status, PLANNED],
      ['?availability=planned', FILTER_LABELS.availability, PLANNED],
    ]
    for (const [search, label, option] of cases) {
      const before = asked().length
      const view = renderGraphRoute(`/browsers${search}`)
      expect(await screen.findByText(NO_FILTER_MATCH, {}, GRAPH_WAIT)).toBeInTheDocument()
      await optionsLoaded()
      expect(shownOption(label), search).toBe(option)
      expect(asked().slice(before).sort()).toEqual([BROWSERS, `${BROWSERS}${search}`, STATUS])
      expect(cards()).toEqual([])
      expect(screen.getByRole('button', { name: CLEAR_FILTERS })).toBeInTheDocument()
      view.unmount()
    }
  }, 20_000)

  it('GP-5: filters combine by deep link and by clicks, and search with a filter is AND', async () => {
    const user = userEvent.setup()
    const both = '/api/graph/v1/browsers?tag=protein_interaction&taxon=32630'
    let view = renderGraphRoute('/browsers?tag=protein_interaction&taxon=32630')
    expect(await screen.findByText(ONE_LISTED, {}, GRAPH_WAIT)).toBeInTheDocument()
    await optionsLoaded()
    expect(cards()).toEqual([TYPED_TITLE])
    expect(asked().sort()).toEqual([BROWSERS, both, STATUS])
    view.unmount()
    let before = asked().length
    view = renderGraphRoute('/browsers')
    await screen.findByText(TWO_LISTED, {}, GRAPH_WAIT)
    await user.selectOptions(select(FILTER_LABELS.taxon), SYNTHETIC_TAXON)
    await screen.findByText(ONE_LISTED, {}, GRAPH_WAIT)
    await user.selectOptions(select(FILTER_LABELS.tag), PROTEIN_INTERACTIONS)
    await waitFor(() => expect(asked().slice(before)).toContain(both), GRAPH_WAIT)
    await screen.findByText(ONE_LISTED, {}, GRAPH_WAIT)
    expect(asked().slice(before)).toEqual([STATUS, BROWSERS, '/api/graph/v1/browsers?taxon=32630', both])
    expect(cards()).toEqual([TYPED_TITLE])
    view.unmount()
    before = asked().length
    renderGraphRoute('/browsers?q=search&tag=gene_regulation')
    expect(await screen.findByText(NO_BOTH_MATCH, {}, GRAPH_WAIT)).toBeInTheDocument()
    await optionsLoaded()
    expect(asked().slice(before).sort()).toEqual([BROWSERS, '/api/graph/v1/browsers?q=search&tag=gene_regulation', STATUS])
    expect(cards()).toEqual([])
  }, 20_000)

  it.each([
    ['?year=abc&utm_source=news', YEAR_ERROR, WITHOUT_YEAR, '/browsers?utm_source=news', true],
    ['?taxon=0&utm_source=news', TAXON_ERROR, WITHOUT_TAXON, '/browsers?utm_source=news', false],
    ['?q=%20&utm_source=news', SEARCH_ERROR, WITHOUT_SEARCH, '/browsers?utm_source=news', false],
    [`?q=${'a'.repeat(201)}&utm_source=news`, SEARCH_ERROR, WITHOUT_SEARCH, '/browsers?utm_source=news', false],
    ['?tag=a&tag=b&utm_source=news', TAG_ERROR, WITHOUT_TAG, '/browsers?utm_source=news', false],
    ['?year=x&q=&utm_source=news', SEARCH_YEAR_ERROR, WITHOUT_SEARCH_YEAR, '/browsers?utm_source=news', false],
    // A valid filter beside the refused name: still no list request, and the link keeps the filter.
    ['?tag=gene_regulation&year=abc&utm_source=news', YEAR_ERROR, WITHOUT_YEAR, '/browsers?tag=gene_regulation&utm_source=news', false],
  ])('GP-6: the address %s sends no list request and links to itself without the names it cannot use', async (search, line, words, href, click) => {
    const user = userEvent.setup()
    const { router } = renderGraphRoute(`/browsers${search}`)
    expect(await screen.findByText(line, {}, GRAPH_WAIT)).toBeInTheDocument()
    await optionsLoaded()
    // A pause after which nothing more is expected: no list request may follow.
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 300))
    })
    expect(asked().sort()).toEqual([BROWSERS, STATUS])
    expect(screen.queryAllByRole('list')).toEqual([])
    const link = screen.getByRole('link', { name: words })
    expect(link).toHaveAttribute('href', href)
    if (!click) return
    const n = asked().length
    await user.click(link)
    await screen.findByText(TWO_LISTED, {}, GRAPH_WAIT)
    expect(document.activeElement).toBe(screen.getByRole('heading', { level: 1, name: TITLE }))
    expect(router.state.location.search).toBe('?utm_source=news')
    expect(router.state.historyAction).toBe('PUSH')
    expect(screen.queryByRole('link', { name: words })).toBeNull()
    expect(asked().slice(n)).toEqual([])
  }, 20_000)

  it('GP-7: a value the API refuses is named from its errors, never its detail, and a 400 naming nothing drops all eight names', async () => {
    const view = renderGraphRoute('/browsers?tag=foo')
    expect(await screen.findByText(TAG_ERROR, {}, GRAPH_WAIT)).toBeInTheDocument()
    await optionsLoaded()
    expect(document.body.textContent ?? '').not.toContain("Unknown tag 'foo'")
    expect(screen.getByRole('link', { name: WITHOUT_TAG })).toHaveAttribute('href', '/browsers')
    expect(shownOption(FILTER_LABELS.tag)).toBe('foo')
    expect(asked().sort()).toEqual([BROWSERS, '/api/graph/v1/browsers?tag=foo', STATUS])
    view.unmount()
    // Every request with a query answers a 400 that names no parameter; the unfiltered options' request keeps its recording.
    server.use(http.get(BROWSERS, ({ request }) =>
      new URL(request.url).search === '' ? undefined : HttpResponse.json({ detail: 'x', errors: [] }, { status: 400 })))
    const before = asked().length
    renderGraphRoute('/browsers?tag=gene_regulation&utm_source=news')
    expect(await screen.findByText(WHOLE_ADDRESS, {}, GRAPH_WAIT)).toBeInTheDocument()
    await optionsLoaded()
    expect(screen.getByRole('link', { name: WHOLE_GALLERY })).toHaveAttribute('href', '/browsers?utm_source=news')
    expect(asked().slice(before).sort()).toEqual([BROWSERS, '/api/graph/v1/browsers?tag=gene_regulation', STATUS])
  }, 20_000)

  it('GP-8: unknown names stay in the address, are never sent, and travel in the card link state', async () => {
    const user = userEvent.setup()
    const { router } = renderGraphRoute('/browsers?utm_source=news&tag=gene_regulation')
    expect(await screen.findByText(ONE_LISTED, {}, GRAPH_WAIT)).toBeInTheDocument()
    await optionsLoaded()
    expect(asked().sort()).toEqual([BROWSERS, '/api/graph/v1/browsers?tag=gene_regulation', STATUS])
    expect(router.state.location.search).toBe('?utm_source=news&tag=gene_regulation')
    const n = asked().length
    await user.click(screen.getByRole('link', { name: TYPED_TITLE }))
    expect(await screen.findByRole('heading', { level: 1, name: TYPED_TITLE }, GRAPH_WAIT)).toBeInTheDocument()
    expect(router.state.location.state).toEqual({ gallerySearch: '?utm_source=news&tag=gene_regulation' })
    expect(asked().slice(n)).toEqual([INSTANCE])
  }, 20_000)

  it('GP-9: a visitor sees the empty-list words and only the Any options', async () => {
    server.use(...typedGraphVisitorHandlers)
    renderGraphRoute('/browsers')
    expect(await screen.findByText(EMPTY_LIST, {}, GRAPH_WAIT)).toBeInTheDocument()
    expect(document.body.textContent ?? '').not.toContain('not enabled')
    for (const name of Object.keys(FILTER_LABELS) as (keyof typeof FILTER_LABELS)[]) {
      expect(optionTexts(FILTER_LABELS[name]), name).toEqual([ANY_OPTION[name]])
    }
    expect(asked()).toEqual([STATUS, BROWSERS])
  }, 20_000)

  it('GP-10: a next page reads as incomplete and is never fetched', async () => {
    serveEvery(makeBrowsersResponse([unlistedInstance], 'syn-next'))
    renderGraphRoute('/browsers')
    expect(await screen.findByText(`${ONE_LISTED} ${INCOMPLETE_LIST}`, {}, GRAPH_WAIT)).toBeInTheDocument()
    // A pause after which nothing more is expected: no cursor request may follow.
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 1000))
    })
    expect(asked().filter((url) => url.startsWith(BROWSERS))).toEqual([BROWSERS])
    expect(asked()).toEqual([STATUS, BROWSERS])
  }, 20_000)

  it('GP-11: each kind and availability looks different in words, badge, visibility and action', async () => {
    const looks: [GraphInstance, string, boolean, string | null][] = [
      [plannedInstance, 'Typed data · Planned', true, null],
      [plannedPlainInstance, 'Typed data · Planned', false, null],
      [retiredInstance, 'Typed data · Retired', true, null],
      [externalInstance, 'External portal', false, null],
      [citationCardInstance, 'Citation card · No data', true, null],
      [unavailableInstance, 'Typed data · Data unavailable', false, null],
      [plannedPortalInstance, "This portal's search · Planned", false, null],
      [retiredPortalInstance, "This portal's search · Retired", false, null],
      [unlistedInstance, 'Typed data · Live', true, UNLISTED],
    ]
    serveEvery(makeBrowsersResponse(looks.map(([instance]) => instance)))
    renderGraphRoute('/browsers')
    expect(await screen.findByText(NINE_LISTED, {}, GRAPH_WAIT)).toBeInTheDocument()
    // The stub's order, which is neither the titles' nor the slugs' alphabetical order: the cards are never re-sorted.
    expect(cards()).toEqual(looks.map(([instance]) => instance.title))
    for (const [instance, state, badge, visibility] of looks) {
      const heading = screen.getByRole('heading', { level: 2, name: instance.title })
      const title = within(heading).getByRole('link', { name: instance.title })
      const card = heading.closest('[data-selectable]') as HTMLElement
      const inCard = within(card)
      expect(inCard.getByText(state), instance.slug).toBeInTheDocument()
      expect(inCard.queryByText(SYNTHETIC_BADGE) !== null, instance.slug).toBe(badge)
      for (const words of [STAFF_ONLY, UNLISTED]) expect(inCard.queryByText(words) !== null, `${instance.slug} ${words}`).toBe(words === visibility)
      const others = inCard.getAllByRole('link').filter((link) => link !== title)
      if (instance === externalInstance) {
        expect(others).toHaveLength(1)
        expect(others[0]).toHaveTextContent('https://portal.example/browse')
        expect(others[0]).toHaveAttribute('href', 'https://portal.example/browse')
        expect(others[0]).toHaveAttribute('target', '_blank')
        expect(others[0].getAttribute('rel')).toBe('noopener noreferrer')
        expect(others[0].parentElement?.textContent).toBe(`${EXTERNAL_ACTION} https://portal.example/browse`)
      } else {
        expect(others, instance.slug).toEqual([])
      }
    }
    expect(screen.getByRole('list').querySelectorAll('img')).toHaveLength(0)
    expect(asked()).toEqual([STATUS, BROWSERS])
  }, 20_000)

  it('GP-12: failures offer Try again, a held retry stays off and focused, and a repeated 429 keeps the button throughout', async () => {
    const user = userEvent.setup()
    const throttled = () => HttpResponse.json({ detail: 'Request was throttled.' }, { status: 429, headers: { 'Retry-After': '7' } })
    // (a) A 429, then a retry whose answer is held until the row releases it.
    let calls = 0
    const second = held()
    server.use(http.get(BROWSERS, async () => {
      calls += 1
      if (calls === 1) return throttled()
      await second.promise
      return HttpResponse.json(galleryBody())
    }))
    let view = renderGraphRoute('/browsers')
    expect(await screen.findByText(THROTTLED_SEVEN, {}, GRAPH_WAIT)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: TRY_AGAIN }))
    await waitFor(() => expect(screen.getByRole('button', { name: TRY_AGAIN })).toHaveAttribute('aria-disabled', 'true'), GRAPH_WAIT)
    expect(document.activeElement).toBe(screen.getByRole('button', { name: TRY_AGAIN }))
    // Never `disabled` (4.7): jsdom keeps focus on a disabled button, where a browser takes it away.
    expect(screen.getByRole('button', { name: TRY_AGAIN })).toBeEnabled()
    second.release()
    expect(await screen.findByText(TWO_LISTED, {}, GRAPH_WAIT)).toBeInTheDocument()
    await waitFor(() => expect(document.activeElement).toBe(countLine()), GRAPH_WAIT)
    expect(asked()).toEqual([STATUS, BROWSERS, BROWSERS])
    view.unmount()
    // (a2) A held retry, then another Biology area: the new list's load offers no Try again, since the button's key names the params.
    server.resetHandlers()
    let filtered = 0
    const third = held()
    server.use(http.get(BROWSERS, async ({ request }) => {
      if (new URL(request.url).search === '') return undefined
      filtered += 1
      if (filtered === 1) return throttled()
      await third.promise
      return HttpResponse.json(galleryBody())
    }))
    const mark = asked().length
    view = renderGraphRoute('/browsers?tag=gene_regulation')
    expect(await screen.findByText(THROTTLED_SEVEN, {}, GRAPH_WAIT)).toBeInTheDocument()
    await optionsLoaded()
    await user.click(screen.getByRole('button', { name: TRY_AGAIN }))
    await waitFor(() => expect(screen.getByRole('button', { name: TRY_AGAIN })).toHaveAttribute('aria-disabled', 'true'), GRAPH_WAIT)
    await user.selectOptions(select(FILTER_LABELS.tag), PROTEIN_INTERACTIONS)
    await waitFor(() => expect(asked()).toContain(`${BROWSERS}?tag=protein_interaction`), GRAPH_WAIT)
    expect(screen.queryByRole('button', { name: TRY_AGAIN })).toBeNull()
    third.release()
    expect(await screen.findByText(TWO_LISTED, {}, GRAPH_WAIT)).toBeInTheDocument()
    expect(asked().slice(mark).sort()).toEqual([
      BROWSERS, `${BROWSERS}?tag=gene_regulation`, `${BROWSERS}?tag=gene_regulation`, `${BROWSERS}?tag=protein_interaction`, STATUS,
    ])
    view.unmount()
    // (b) to (d): a JSON 500, a network failure after F1's one retry, and a 200 that is no gallery body.
    const failures: [() => Response, string, number][] = [
      [() => HttpResponse.json({ detail: 'x' }, { status: 500 }), LIST_ERROR, 1],
      [() => HttpResponse.error(), SERVER_UNREACHABLE, 2],
      [() => HttpResponse.json({}), LIST_ERROR, 1],
    ]
    for (const [reply, words, sent] of failures) {
      server.resetHandlers()
      server.use(http.get(BROWSERS, reply))
      const before = asked().length
      view = renderGraphRoute('/browsers')
      expect(await screen.findByText(words, {}, GRAPH_WAIT)).toBeInTheDocument()
      expect(screen.getByRole('button', { name: TRY_AGAIN })).toBeInTheDocument()
      expect(asked().slice(before)).toEqual([STATUS, ...Array.from({ length: sent }, () => BROWSERS)])
      view.unmount()
    }
    // (e) Every answer a 429: the pressed button is never removed, and once the second 429 has come it is on again.
    server.resetHandlers()
    const before = asked().length
    const answeredBefore = answers.answers.length
    const answer = held()
    let replies = 0
    server.use(http.get(BROWSERS, async () => {
      replies += 1
      if (replies > 1) await answer.promise
      return throttled()
    }))
    renderGraphRoute('/browsers')
    const button = await screen.findByRole('button', { name: TRY_AGAIN }, GRAPH_WAIT)
    let gone = false
    const watch = new MutationObserver(() => {
      if (!document.body.contains(button)) gone = true
    })
    watch.observe(document.body, { childList: true, subtree: true })
    try {
      await user.click(button)
      await waitFor(() => expect(button).toHaveAttribute('aria-disabled', 'true'), GRAPH_WAIT)
      expect(button).toBeEnabled()
      answer.release()
      await waitFor(() => expect(asked().slice(before).filter((url) => url === BROWSERS)).toHaveLength(2), GRAPH_WAIT)
      await waitFor(() => expect(answers.answers.slice(answeredBefore).filter((entry) => entry === `429 ${BROWSERS}`)).toHaveLength(2), GRAPH_WAIT)
      await waitFor(() => expect(button).not.toHaveAttribute('aria-disabled'), GRAPH_WAIT)
      expect(gone).toBe(false)
      expect(document.body.contains(button)).toBe(true)
      expect(button.style.borderStyle).toBe('solid')
      expect(screen.getByText(THROTTLED_SEVEN)).toBeInTheDocument()
      expect(asked().slice(before)).toEqual([STATUS, BROWSERS, BROWSERS])
    } finally {
      watch.disconnect()
    }
  }, 20_000)

  it('GP-13: a 404 while on rechecks status once per failure: off reads not enabled, on reads the list error with Try again', async () => {
    const user = userEvent.setup()
    let statusCalls = 0
    server.use(
      http.get(BROWSERS, () => HttpResponse.json({ detail: 'Not found.' }, { status: 404 })),
      http.get(STATUS, () => {
        statusCalls += 1
        return HttpResponse.json(recorded<JsonBodyType>(statusCalls === 1 ? 'status-on' : 'status-off').body)
      }),
    )
    const view = renderGraphRoute('/browsers')
    expect(await screen.findByText(NOT_ENABLED, {}, GRAPH_WAIT)).toBeInTheDocument()
    expect(statusCalls).toBe(2)
    expect(asked()).toEqual([STATUS, BROWSERS, STATUS])
    view.unmount()
    server.resetHandlers()
    server.use(http.get(BROWSERS, () => HttpResponse.json({ detail: 'Not found.' }, { status: 404 })))
    const before = asked().length
    renderGraphRoute('/browsers')
    expect(await screen.findByText(LIST_ERROR, {}, GRAPH_WAIT)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: TRY_AGAIN })).toBeInTheDocument()
    // A pause after which nothing more is expected: the recheck must not repeat.
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 1000))
    })
    expect(asked().slice(before).filter((url) => url === STATUS)).toHaveLength(2)
    expect(asked().slice(before)).toEqual([STATUS, BROWSERS, STATUS])
    // A Try again answered by another 404 rechecks once more: each failed fetch is a failure of its own.
    await user.click(screen.getByRole('button', { name: TRY_AGAIN }))
    await waitFor(() => expect(asked().slice(before)).toEqual([STATUS, BROWSERS, STATUS, BROWSERS, STATUS]), GRAPH_WAIT)
    expect(await screen.findByText(LIST_ERROR, {}, GRAPH_WAIT)).toBeInTheDocument()
  }, 20_000)

  it('GP-14: Clear filters pushes the address without the filters, keeps the search, and focuses the first select', async () => {
    const user = userEvent.setup()
    serveEvery(recorded<JsonBodyType>('browsers-tag-gene').body)
    const { router } = renderGraphRoute('/browsers?q=search&tag=gene_regulation&year=2026&utm_source=news')
    expect(await screen.findByText(ONE_LISTED, {}, GRAPH_WAIT)).toBeInTheDocument()
    await optionsLoaded()
    expect(asked().sort()).toEqual([BROWSERS, '/api/graph/v1/browsers?q=search&tag=gene_regulation&year=2026', STATUS])
    const n = asked().length
    await user.click(screen.getByRole('button', { name: CLEAR_FILTERS }))
    await waitFor(() => expect(router.state.location.search).toBe('?q=search&utm_source=news'), GRAPH_WAIT)
    expect(router.state.historyAction).toBe('PUSH')
    await waitFor(() => expect(screen.queryByRole('button', { name: CLEAR_FILTERS })).toBeNull(), GRAPH_WAIT)
    expect(document.activeElement).toBe(select(FILTER_LABELS.tag))
    await waitFor(() => expect(asked().slice(n)).toEqual(['/api/graph/v1/browsers?q=search']), GRAPH_WAIT)
  }, 20_000)

  it('GP-15: every hostile string of a card is literal text, and no address outside http(s) or a router path reaches href', async () => {
    serveEvery(makeBrowsersResponse([hostileInstance]))
    const { container } = renderGraphRoute('/browsers')
    expect(await screen.findByText(ONE_LISTED, {}, GRAPH_WAIT)).toBeInTheDocument()
    // The filter options hold the same strings, so each check stays inside the card.
    const title = screen.getByRole('link', { name: hostile('title') })
    const card = within(title.closest('[data-selectable]') as HTMLElement)
    expect(card.getByText(hostile('summary'))).toBeInTheDocument()
    expect(card.getByText(hostile('tags[0]'))).toBeInTheDocument()
    expect(card.getByText(hostile('relationship_types[0]').split('_').join(' '))).toBeInTheDocument()
    expect(card.getByText(`${isolateText(hostile('publications[0].title'))}${SYNTHETIC_SUFFIX}`)).toBeInTheDocument()
    expect(container.querySelector('[data-hostile]')).toBeNull()
    expect(strayAddresses()).toEqual([])
    expect(card.getByText(`${EXTERNAL_ACTION} javascript:alert(1)`)).toBeInTheDocument()
    expect(asked()).toEqual([STATUS, BROWSERS])
  }, 20_000)

  it('GP-16: a deep link moves no focus and does not scroll', async () => {
    renderGraphRoute('/browsers')
    expect(await screen.findByText(TWO_LISTED, {}, GRAPH_WAIT)).toBeInTheDocument()
    expect(document.activeElement).toBe(document.body)
    expect(scrolls).not.toHaveBeenCalled()
    expect(asked()).toEqual([STATUS, BROWSERS])
  }, 20_000)

  it('GP-17: an options body that fails the guard gives no option, beside a sound filtered list', async () => {
    const bad = { ...galleryBody(), instances: [{ slug: 'syn-bad', kind: 'typed', availability: 'live' }] }
    server.use(http.get(BROWSERS, ({ request }) => (new URL(request.url).search === '' ? HttpResponse.json(bad) : undefined)))
    renderGraphRoute('/browsers?tag=gene_regulation')
    expect(await screen.findByText(ONE_LISTED, {}, GRAPH_WAIT)).toBeInTheDocument()
    await waitFor(() => expect(answers.answers).toContain(`200 ${BROWSERS}`), GRAPH_WAIT)
    expect(cards()).toEqual([TYPED_TITLE])
    expect(optionTexts(FILTER_LABELS.tag)).toEqual([ANY_OPTION.tag, GENE_REGULATION])
    for (const name of Object.keys(FILTER_LABELS).filter((key) => key !== 'tag') as (keyof typeof FILTER_LABELS)[]) {
      expect(optionTexts(FILTER_LABELS[name]), name).toEqual([ANY_OPTION[name]])
    }
    expect(screen.queryByText(PAGE_UNKNOWN)).toBeNull()
    expect(asked().sort()).toEqual([BROWSERS, '/api/graph/v1/browsers?tag=gene_regulation', STATUS])
  }, 20_000)

  it('GP-18: a repeated search, submitted unchanged, is written once when the page can use it, and not otherwise', async () => {
    const user = userEvent.setup()
    // The box shows the first value, which the page can use alone: Search writes it once and sends it.
    let view = renderGraphRoute('/browsers?q=search&q=b&utm_source=news')
    expect(await screen.findByText(SEARCH_ERROR, {}, GRAPH_WAIT)).toBeInTheDocument()
    expect((screen.getByRole('searchbox', { name: SEARCH_LABEL }) as HTMLInputElement).value).toBe('search')
    await waitFor(() => expect(asked().sort()).toEqual([BROWSERS, STATUS]), GRAPH_WAIT)
    let n = asked().length
    await user.click(screen.getByRole('button', { name: SEARCH_BUTTON }))
    expect(await screen.findByText(ONE_LISTED, {}, GRAPH_WAIT)).toBeInTheDocument()
    expect(view.router.state.location.search).toBe('?q=search&utm_source=news')
    expect(view.router.state.historyAction).toBe('PUSH')
    expect(asked().slice(n)).toEqual(['/api/graph/v1/browsers?q=search'])
    view.unmount()
    // A first value holding a control character is refused alone too, so Search writes nothing.
    n = asked().length
    view = renderGraphRoute('/browsers?q=a%01b&q=c&utm_source=news')
    expect(await screen.findByText(SEARCH_ERROR, {}, GRAPH_WAIT)).toBeInTheDocument()
    await waitFor(() => expect(asked().slice(n).sort()).toEqual([BROWSERS, STATUS]), GRAPH_WAIT)
    const key = view.router.state.location.key
    await user.click(screen.getByRole('button', { name: SEARCH_BUTTON }))
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 300))
    })
    expect(view.router.state.location.key).toBe(key)
    expect(asked().slice(n).sort()).toEqual([BROWSERS, STATUS])
  }, 20_000)
})
