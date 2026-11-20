import { act, cleanup, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import type { JsonBodyType } from 'msw'
import { createPath } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { MockInstance } from 'vitest'
import { graphStatusOptions } from '../../api/graph/status'
import { typedGraphOffHandlers } from '../../mocks/handlers/typedGraph'
import { server } from '../../mocks/server'
import { useAuthStore } from '../../store/authStore'
import { GRAPH_WAIT, newGraphClient, recordRequests, recorded } from '../../test/graphHooks'
import type { RequestLog } from '../../test/graphHooks'
import { SHELL_GALLERY_LINK, SHELL_INSTANCE_LINK, TEST_SHELL, recordAnswers, renderGraphRoute } from '../../test/graphRoutes'
import type { GraphStatus } from '../graph/types'

// A factory runs on its module's first import, so the counters say which page chunks a row loaded. They need
// each import inside the rows, so this file warms nothing up, and RT-1 to RT-1d, which must load no
// chunk, come first.
const loads = vi.hoisted(() => ({ gallery: 0, instance: 0 }))
vi.mock('./GalleryPage', async (importOriginal) => {
  loads.gallery += 1
  return importOriginal()
})
vi.mock('./InstancePage', async (importOriginal) => {
  loads.instance += 1
  return importOriginal()
})
// The network's request still goes out through the workspace's hook; only the drawing is left out.
vi.mock('../graph/network/TypedNetwork', () => ({ TypedNetwork: () => null }))

let requests: RequestLog
let answers: { answers: string[]; stop: () => void }
let scrolls: MockInstance

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

// Words copied from the spec's sections 3.1 to 3.5 and 3.7, and the taxon words of 6.2, never from the modules under test.
const TITLE = 'Browsers'
const PAGE_LOADING = 'Loading this page…'
const PAGE_UNKNOWN = 'This page could not be loaded.'
const TRY_AGAIN = 'Try again'
const RELOAD = 'Reload'
const NOT_ENABLED = 'Browsers are not enabled on this portal.'
const SEARCH_LABEL = 'Search titles and summaries'
const TAG_LABEL = 'Biology area'
const TAXON_LABEL = 'Organism'
const ANY_TAG = 'Any biology area'
const ONE_LISTED = '1 browser listed.'
const TWO_LISTED = '2 browsers listed.'
const GENE_REGULATION = 'Gene regulation'
const PROTEIN_INTERACTIONS = 'Protein interactions'
const PORTAL_SEARCH_ACTION = "Open this portal's search"
const NOT_AVAILABLE = 'This browser is not available to you.'
const BACK_LINK = 'Back to the gallery'
const NETWORK_HEADING = 'Network'
const SYNTHETIC_TAXON = 'synthetic construct (taxid 32630)'
// F3's row name (spec 12.0, Words: F2's and F3's names take no isolates).
const TF1_G1 = 'Transcriptional regulation: SYN-TF1 (Gene) to SYN-G1 (Gene)'
// The recorded titles, and React Router's own words for a path no route matches.
const TYPED_TITLE = 'Synthetic regulation example'
const PORTAL_TITLE = 'Protein interactions on this portal'
const NO_ROUTE = '404 Not Found'

const STATUS = '/api/graph/v1/status'
const BROWSERS = '/api/graph/v1/browsers'
const INSTANCE = '/api/graph/v1/browsers/synthetic-regulation'
const NETWORK = '/api/graph/v1/network?seeds=entity:7&browser=synthetic-regulation'
// Each page's first lazy render in this file waits for its modules to compile (spec 12.0, Timeouts).
const FIRST_LAZY_WAIT = { timeout: 30_000 }

const asked = () => requests.urls.map((url) => url.pathname + url.search)
const statusOn = () => recorded<JsonBodyType>('status-on').body
const h1 = (name: string) => screen.getByRole('heading', { level: 1, name })
const optionTexts = (select: HTMLSelectElement) => Array.from(select.options, (option) => option.textContent)

/** A status handler that answers once the row releases it; `first`, when given, answers the first request at once. */
function heldStatus(first?: () => Response) {
  let release: () => void = () => {}
  const held = new Promise<void>((resolve) => {
    release = resolve
  })
  let calls = 0
  server.use(http.get(STATUS, async () => {
    calls += 1
    if (calls === 1 && first !== undefined) return first()
    await held
    return HttpResponse.json(statusOn())
  }))
  return { release: () => release() }
}

describe('the typed routes', () => {
  it('RT-1: an off portal shows the off sentence on both routes, asks only status and loads no page chunk', async () => {
    server.use(...typedGraphOffHandlers)
    const gallery = renderGraphRoute('/browsers', { vocabularies: false })
    expect(await screen.findByText(NOT_ENABLED, {}, GRAPH_WAIT)).toBeInTheDocument()
    expect(h1(TITLE)).toBeInTheDocument()
    expect(asked()).toEqual([STATUS])
    expect(loads).toEqual({ gallery: 0, instance: 0 })
    gallery.unmount()
    const before = asked().length
    renderGraphRoute('/browsers/synthetic-regulation', { vocabularies: false })
    expect(await screen.findByText(NOT_ENABLED, {}, GRAPH_WAIT)).toBeInTheDocument()
    expect(h1(TITLE)).toBeInTheDocument()
    expect(asked().slice(before)).toEqual([STATUS])
    expect(loads).toEqual({ gallery: 0, instance: 0 })
  }, 20_000)

  it('RT-1b: while status is loading no not-enabled or not-available words show and no page chunk loads', async () => {
    const status = heldStatus()
    const view = renderGraphRoute('/browsers')
    expect(await screen.findByText(PAGE_LOADING, {}, GRAPH_WAIT)).toBeInTheDocument()
    expect(h1(TITLE)).toBeInTheDocument()
    expect(screen.queryByText(NOT_ENABLED)).toBeNull()
    expect(screen.queryByText(NOT_AVAILABLE)).toBeNull()
    expect(asked()).toEqual([STATUS])
    expect(loads).toEqual({ gallery: 0, instance: 0 })
    view.unmount()
    expect(loads).toEqual({ gallery: 0, instance: 0 })
    // Released only now, so that no request outlives the row.
    status.release()
    await waitFor(() => expect(answers.answers).toContain(`200 ${STATUS}`), GRAPH_WAIT)
    expect(loads).toEqual({ gallery: 0, instance: 0 })
  }, 20_000)

  it('RT-1c: an unknown status offers Try again, asks only status and loads no page chunk', async () => {
    server.use(http.get(STATUS, () => HttpResponse.html('<html></html>', { status: 500 })))
    renderGraphRoute('/browsers/synthetic-regulation')
    expect(await screen.findByText(PAGE_UNKNOWN, {}, GRAPH_WAIT)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: TRY_AGAIN })).toBeInTheDocument()
    expect(asked()).toEqual([STATUS])
    expect(loads).toEqual({ gallery: 0, instance: 0 })
  }, 20_000)

  it('RT-1d: with resolve, entities, network and relationships on, browsers off shows the off sentence on both routes, asks only status and loads no page chunk', async () => {
    const body = recorded<GraphStatus>('status-on').body
    server.use(http.get(STATUS, () => HttpResponse.json({ ...body, features: { ...body.features, browsers: false } })))
    const gallery = renderGraphRoute('/browsers', { vocabularies: false })
    expect(await screen.findByText(NOT_ENABLED, {}, GRAPH_WAIT)).toBeInTheDocument()
    expect(asked()).toEqual([STATUS])
    gallery.unmount()
    const before = asked().length
    renderGraphRoute('/browsers/synthetic-regulation', { vocabularies: false })
    expect(await screen.findByText(NOT_ENABLED, {}, GRAPH_WAIT)).toBeInTheDocument()
    expect(asked().slice(before)).toEqual([STATUS])
    expect(loads).toEqual({ gallery: 0, instance: 0 })
  }, 20_000)

  it('RT-2: the shell, each page chunk on its first visit, the fallback with its h1, and no status request when prefilled', async () => {
    const user = userEvent.setup()
    const client = newGraphClient()
    client.setQueryData(graphStatusOptions().queryKey, recorded<GraphStatus>('status-on').body)
    const { router } = renderGraphRoute('/', { client })
    expect(screen.getByText(TEST_SHELL)).toBeInTheDocument()
    expect(asked()).toEqual([])
    expect(loads).toEqual({ gallery: 0, instance: 0 })
    // Each element that takes focus: the fallback shows on each page's first visit, and its heading takes none.
    const focused: Element[] = []
    const record = (event: FocusEvent) => {
      focused.push(event.target as Element)
    }
    document.addEventListener('focusin', record)
    try {
      await act(() => router.navigate('/browsers'))
      // The gate is on at once, so the one status line is the fallback's, beside its own h1.
      const fallback = screen.getByRole('status')
      expect(fallback.textContent).toBe(PAGE_LOADING)
      expect(h1(TITLE).parentElement).toBe(fallback.parentElement)
      expect(await screen.findByRole('searchbox', { name: SEARCH_LABEL }, FIRST_LAZY_WAIT)).toBeInTheDocument()
      expect(loads).toEqual({ gallery: 1, instance: 0 })
      await waitFor(() => expect(document.activeElement).toBe(h1(TITLE)), GRAPH_WAIT)
      const gallery = h1(TITLE)
      const link = await screen.findByRole('link', { name: TYPED_TITLE }, GRAPH_WAIT)
      await user.click(link)
      expect(screen.getByText(PAGE_LOADING)).toBeInTheDocument()
      expect(router.state.location.pathname).toBe('/browsers/synthetic-regulation')
      const title = await screen.findByRole('heading', { level: 1, name: TYPED_TITLE }, FIRST_LAZY_WAIT)
      await waitFor(() => expect(document.activeElement).toBe(title), GRAPH_WAIT)
      expect(loads).toEqual({ gallery: 1, instance: 1 })
      expect(asked()).toEqual([BROWSERS, INSTANCE])
      // Compared one by one: toEqual compares elements by isEqualNode, and the fallback's h1 equals the gallery's.
      expect(focused).toHaveLength(3)
      expect(focused[0]).toBe(gallery)
      expect(focused[1]).toBe(link)
      expect(focused[2]).toBe(title)
    } finally {
      document.removeEventListener('focusin', record)
    }
  }, 70_000)

  it('RT-3: an off portal at a filtered address asks only status and leaves the address as it is', async () => {
    server.use(...typedGraphOffHandlers)
    const { router } = renderGraphRoute('/browsers?tag=gene_regulation&taxon=32630')
    expect(await screen.findByText(NOT_ENABLED, {}, GRAPH_WAIT)).toBeInTheDocument()
    expect(asked()).toEqual([STATUS])
    expect(router.state.location.search).toBe('?tag=gene_regulation&taxon=32630')
  }, 20_000)

  it('RT-4: a held status reads Loading this page, with no not-found words; released, the gallery shows', async () => {
    const status = heldStatus()
    renderGraphRoute('/browsers')
    expect((await screen.findByRole('status', {}, GRAPH_WAIT)).textContent).toBe(PAGE_LOADING)
    expect(h1(TITLE)).toBeInTheDocument()
    expect(screen.queryByText(NOT_ENABLED)).toBeNull()
    expect(screen.queryByText(NOT_AVAILABLE)).toBeNull()
    expect(asked()).toEqual([STATUS])
    status.release()
    expect(await screen.findByRole('searchbox', { name: SEARCH_LABEL }, GRAPH_WAIT)).toBeInTheDocument()
    // The list's own answer, so that no request outlives the row.
    expect(await screen.findByText(TWO_LISTED, {}, GRAPH_WAIT)).toBeInTheDocument()
    expect(asked()).toEqual([STATUS, BROWSERS])
  }, 20_000)

  it('RT-5: an unknown status, then a held Try again stays off and focused; released, the page shows and focus is on body', async () => {
    const user = userEvent.setup()
    const status = heldStatus(() => HttpResponse.html('<html></html>', { status: 500 }))
    renderGraphRoute('/browsers/synthetic-regulation')
    const button = await screen.findByRole('button', { name: TRY_AGAIN }, GRAPH_WAIT)
    expect(screen.getByText(PAGE_UNKNOWN)).toBeInTheDocument()
    expect(screen.queryByText(NOT_AVAILABLE)).toBeNull()
    expect(screen.queryByText(NOT_ENABLED)).toBeNull()
    expect(asked()).toEqual([STATUS])
    await user.click(button)
    await waitFor(() => expect(screen.getByText(PAGE_LOADING)).toBeInTheDocument(), GRAPH_WAIT)
    const kept = screen.getByRole('button', { name: TRY_AGAIN })
    expect(kept).toBe(button)
    expect(kept).toHaveAttribute('aria-disabled', 'true')
    expect(kept.style.borderStyle).toBe('dashed')
    expect(document.activeElement).toBe(kept)
    // Never `disabled` (4.7): jsdom keeps focus on a disabled button, where a browser takes it away.
    expect(kept).toBeEnabled()
    status.release()
    expect(await screen.findByRole('heading', { level: 1, name: TYPED_TITLE }, GRAPH_WAIT)).toBeInTheDocument()
    expect(asked().filter((url) => url === STATUS)).toHaveLength(2)
    expect(asked()).toEqual([STATUS, STATUS, INSTANCE])
    expect(document.activeElement).toBe(document.body)
  }, 20_000)

  it('RT-6: a JSON 429 status reads as unknown', async () => {
    server.use(http.get(STATUS, () => HttpResponse.json({ detail: 'Request was throttled.' }, { status: 429 })))
    renderGraphRoute('/browsers')
    expect(await screen.findByText(PAGE_UNKNOWN, {}, GRAPH_WAIT)).toBeInTheDocument()
    expect(asked()).toEqual([STATUS])
  }, 20_000)

  it('RT-7: a filtered gallery survives a reload, the same controls, card and requests in order', async () => {
    const first = renderGraphRoute('/browsers?tag=protein_interaction&taxon=32630')
    const check = async (base: number) => {
      expect(await screen.findByText(ONE_LISTED, {}, GRAPH_WAIT)).toBeInTheDocument()
      await waitFor(() => expect(asked().length).toBe(base + 3), GRAPH_WAIT)
      const tag = screen.getByRole('combobox', { name: TAG_LABEL }) as HTMLSelectElement
      const taxon = screen.getByRole('combobox', { name: TAXON_LABEL }) as HTMLSelectElement
      // The options come from the unfiltered list, so the controls are whole once it has answered too.
      await waitFor(() => expect(optionTexts(tag)).toEqual([ANY_TAG, GENE_REGULATION, PROTEIN_INTERACTIONS]), GRAPH_WAIT)
      expect(tag.selectedOptions[0].textContent).toBe(PROTEIN_INTERACTIONS)
      expect(taxon.selectedOptions[0].textContent).toBe(SYNTHETIC_TAXON)
      expect(screen.getAllByRole('heading', { level: 2 }).map((node) => node.textContent)).toEqual([TYPED_TITLE])
    }
    await check(0)
    const once = asked()
    first.unmount()
    renderGraphRoute(createPath(first.router.state.location), { state: first.router.state.location.state })
    await check(once.length)
    expect(asked().slice(once.length)).toEqual(once)
  }, 20_000)

  it('RT-8: an instance with seeds survives a reload, the network and its table row included', async () => {
    const check = async () => {
      expect(await screen.findByRole('heading', { level: 1, name: TYPED_TITLE }, GRAPH_WAIT)).toBeInTheDocument()
      expect(screen.getByRole('heading', { level: 2, name: NETWORK_HEADING })).toBeInTheDocument()
      expect(await screen.findByRole('button', { name: TF1_G1 }, GRAPH_WAIT)).toBeInTheDocument()
    }
    const first = renderGraphRoute('/browsers/synthetic-regulation?seeds=entity%3A7')
    await check()
    const once = asked()
    expect(once).toEqual([STATUS, INSTANCE, NETWORK])
    first.unmount()
    renderGraphRoute(createPath(first.router.state.location), { state: first.router.state.location.state })
    await check()
    expect(asked().slice(once.length)).toEqual(once)
  }, 20_000)

  it("RT-9: a path no route matches keeps React Router's default page, with no F4 word and no request", async () => {
    renderGraphRoute('/browsers/a/b')
    expect(await screen.findByText(NO_ROUTE, {}, GRAPH_WAIT)).toBeInTheDocument()
    expect(screen.queryByRole('heading', { level: 1, name: TITLE })).toBeNull()
    for (const words of [PAGE_LOADING, PAGE_UNKNOWN, TRY_AGAIN, RELOAD, NOT_ENABLED, NOT_AVAILABLE]) {
      expect(screen.queryByText(words)).toBeNull()
    }
    expect(asked()).toEqual([])
    // The allowance (spec 12.0): React Router's one call, asserted here and cleared, so any other call fails.
    const errors = vi.mocked(console.error).mock.calls.map((args) => String(args[0]))
    expect(errors).toHaveLength(1)
    expect(errors[0].startsWith('Error handled by React Router default ErrorBoundary:')).toBe(true)
    vi.mocked(console.error).mockClear()
  }, 20_000)

  it('RT-10: a card click focuses the link, then the title once; Back focuses the gallery h1 and scrolls; a select keeps focus', async () => {
    const user = userEvent.setup()
    const { router } = renderGraphRoute('/')
    await act(() => router.navigate('/browsers'))
    await screen.findByText(TWO_LISTED, {}, GRAPH_WAIT)
    await waitFor(() => expect(document.activeElement).toBe(h1(TITLE)), GRAPH_WAIT)
    scrolls.mockClear()
    const focused: Element[] = []
    const record = (event: FocusEvent) => {
      focused.push(event.target as Element)
    }
    document.addEventListener('focusin', record)
    try {
      const link = screen.getByRole('link', { name: TYPED_TITLE })
      await user.click(link)
      const title = await screen.findByRole('heading', { level: 1, name: TYPED_TITLE }, GRAPH_WAIT)
      await waitFor(() => expect(document.activeElement).toBe(title), GRAPH_WAIT)
      expect(focused).toEqual([link, title])
      expect(scrolls).toHaveBeenCalledTimes(1)
      expect(scrolls).toHaveBeenCalledWith(0, 0)
      await act(() => router.navigate(-1))
      await screen.findByText(TWO_LISTED, {}, GRAPH_WAIT)
      await waitFor(() => expect(document.activeElement).toBe(h1(TITLE)), GRAPH_WAIT)
      expect(scrolls).toHaveBeenCalledTimes(2)
      expect(scrolls).toHaveBeenLastCalledWith(0, 0)
      const select = screen.getByRole('combobox', { name: TAG_LABEL })
      await user.selectOptions(select, GENE_REGULATION)
      await screen.findByText(ONE_LISTED, {}, GRAPH_WAIT)
      expect(document.activeElement).toBe(select)
      expect(scrolls).toHaveBeenCalledTimes(2)
      // Back reads the gallery and status from the cache, and the select adds its filtered list only.
      expect(asked()).toEqual([STATUS, BROWSERS, INSTANCE, `${BROWSERS}?tag=gene_regulation`])
    } finally {
      document.removeEventListener('focusin', record)
    }
  }, 20_000)

  it('RT-11: a deep link moves no focus and does not scroll', async () => {
    renderGraphRoute('/browsers/synthetic-regulation')
    expect(await screen.findByRole('heading', { level: 1, name: TYPED_TITLE }, GRAPH_WAIT)).toBeInTheDocument()
    expect(document.activeElement).toBe(document.body)
    expect(scrolls).not.toHaveBeenCalled()
    expect(asked()).toEqual([STATUS, INSTANCE])
  }, 20_000)

  it("RT-12: the way back carries the gallery's search; on a deep link it is the whole gallery", async () => {
    const user = userEvent.setup()
    const first = renderGraphRoute('/browsers?tag=gene_regulation')
    await user.click(await screen.findByRole('link', { name: TYPED_TITLE }, GRAPH_WAIT))
    expect(await screen.findByRole('heading', { level: 1, name: TYPED_TITLE }, GRAPH_WAIT)).toBeInTheDocument()
    const back = screen.getByRole('link', { name: BACK_LINK })
    expect(back).toHaveAttribute('href', '/browsers?tag=gene_regulation')
    await user.click(back)
    await screen.findByText(ONE_LISTED, {}, GRAPH_WAIT)
    const tag = screen.getByRole('combobox', { name: TAG_LABEL }) as HTMLSelectElement
    expect(tag.selectedOptions[0].textContent).toBe(GENE_REGULATION)
    await waitFor(() => expect(document.activeElement).toBe(h1(TITLE)), GRAPH_WAIT)
    // The filtered list and its options go out in no set order (spec 12.0, Exact requests); the way back reads the cache.
    expect([...asked()].sort()).toEqual([STATUS, BROWSERS, `${BROWSERS}?tag=gene_regulation`, INSTANCE].sort())
    const before = asked().length
    first.unmount()
    renderGraphRoute('/browsers/synthetic-regulation')
    expect(await screen.findByRole('heading', { level: 1, name: TYPED_TITLE }, GRAPH_WAIT)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: BACK_LINK })).toHaveAttribute('href', '/browsers')
    expect(asked().slice(before)).toEqual([STATUS, INSTANCE])
  }, 20_000)

  it('RT-13: under a basename the portal card links to the portal search and its own page', async () => {
    renderGraphRoute('/openpip/browsers', { basename: '/openpip/' })
    const title = await screen.findByRole('link', { name: PORTAL_TITLE }, GRAPH_WAIT)
    expect(title).toHaveAttribute('href', '/openpip/browsers/protein-interactions')
    const card = title.closest('li') as HTMLElement
    expect(within(card).getByRole('link', { name: PORTAL_SEARCH_ACTION })).toHaveAttribute('href', '/openpip/search')
    // The basename moves the router's paths only: the API keeps its own base.
    expect(asked()).toEqual([STATUS, BROWSERS])
  }, 20_000)

  it('RT-14: a link outside main moves focus to the page h1 and scrolls, for the gallery and for an instance', async () => {
    const user = userEvent.setup()
    renderGraphRoute('/')
    const shell = within(screen.getByRole('navigation'))
    // Each element that takes focus: status is not cached yet, so the gate shows its loading state first.
    const focused: Element[] = []
    const record = (event: FocusEvent) => {
      focused.push(event.target as Element)
    }
    document.addEventListener('focusin', record)
    try {
      const galleryLink = shell.getByRole('link', { name: SHELL_GALLERY_LINK })
      await user.click(galleryLink)
      await screen.findByText(TWO_LISTED, {}, GRAPH_WAIT)
      await waitFor(() => expect(document.activeElement).toBe(h1(TITLE)), GRAPH_WAIT)
      // Compared one by one: toEqual compares elements by isEqualNode, and the gate's h1 equals the gallery's.
      expect(focused).toHaveLength(2)
      expect(focused[0]).toBe(galleryLink)
      expect(focused[1]).toBe(h1(TITLE))
      expect(scrolls).toHaveBeenCalledTimes(1)
      expect(scrolls).toHaveBeenLastCalledWith(0, 0)
      const instanceLink = shell.getByRole('link', { name: SHELL_INSTANCE_LINK })
      await user.click(instanceLink)
      const title = await screen.findByRole('heading', { level: 1, name: TYPED_TITLE }, GRAPH_WAIT)
      await waitFor(() => expect(document.activeElement).toBe(title), GRAPH_WAIT)
      expect(focused).toHaveLength(4)
      expect(focused[2]).toBe(instanceLink)
      expect(focused[3]).toBe(title)
      expect(scrolls).toHaveBeenCalledTimes(2)
      expect(scrolls).toHaveBeenLastCalledWith(0, 0)
      expect(asked()).toEqual([STATUS, BROWSERS, INSTANCE])
    } finally {
      document.removeEventListener('focusin', record)
    }
  }, 20_000)
})
