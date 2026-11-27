import { act, cleanup, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import type { JsonBodyType } from 'msw'
import { createPath } from 'react-router-dom'
import ts from 'typescript'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { MockInstance } from 'vitest'
import { graphStatusOptions } from '../../../api/graph/status'
import { typedGraphOffHandlers } from '../../../mocks/handlers/typedGraph'
import { server } from '../../../mocks/server'
import { useAuthStore } from '../../../store/authStore'
import { GRAPH_WAIT, newGraphClient, recordRequests, recorded } from '../../../test/graphHooks'
import type { RequestLog } from '../../../test/graphHooks'
import { SHELL_EXPLORE_LINK, TEST_SHELL, recordAnswers, renderGraphRoute } from '../../../test/graphRoutes'
import { BrowserInstanceRoute, BrowsersGalleryRoute } from '../../browsers/BrowsersRoutes'
import { EntityLookupRoute, EntityRoute } from '../../entities/EntitiesRoutes'
import { ExploreRoute } from '../../explore/ExploreRoutes'
import type { GraphStatus } from '../types'
import { isolateText } from '../words/isolateText'
import { TYPED_ROUTES } from './typedRoutes'
import { TypedRouteError } from './TypedRouteStates'

// A factory runs on its module's first import, so the counters say which page chunks a row loaded. They need each
// import inside the rows, so this file warms nothing up, and XT-1 to XT-4, which must load no chunk, come first.
const loads = vi.hoisted(() => ({ explore: 0, entities: 0 }))
vi.mock('../../explore/ExplorePage', async (importOriginal) => {
  loads.explore += 1
  return importOriginal()
})
vi.mock('../../entities/EntityPages', async (importOriginal) => {
  loads.entities += 1
  return importOriginal()
})
// The network's request still goes out through the workspace's hook; only the drawing is left out.
vi.mock('../network/TypedNetwork', () => ({ TypedNetwork: () => null }))

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

// Words copied from X1's spec, sections 11.4, 7.12, 8.10 and 10.10, F4's page words and React Router's own, never from the modules under test.
const EXPLORE_TITLE = 'Explore the unified network'
const EXPLORE_NOT_ENABLED = 'The unified network is not enabled on this portal.'
const ENTITY_TITLE = 'Entity'
const ENTITY_NOT_ENABLED = 'Entity pages are not enabled on this portal.'
const ENTITY_LOOKUP_TITLE = 'Find an entity by its identifier'
const ENTITY_LOOKUP_NOT_ENABLED = 'Finding an entity by its identifier is not enabled on this portal.'
const PAGE_LOADING = 'Loading this page…'
const PAGE_UNKNOWN = 'This page could not be loaded.'
const TRY_AGAIN = 'Try again'
const SEARCH_FORM_NAME = 'Search for entities'
const NO_SEED_PROMPT = 'Search for one or more entities to draw their network.'
const CHOSEN_LABEL = 'Chosen entities'
const NEWER_VERSION_LINK = 'Open the explorer with no entities chosen'
const EXPLORE_ENTITY = 'Explore this entity in the unified network'
const openText = (name: string) => `Open ${isolateText(name)}`
const FOLLOW_LABEL = 'Follow gene-to-protein links'
const OUTCOME_LABEL = 'Show tested-absent results'
const NO_ROUTE = '404 Not Found'
const TF1 = 'SYN-TF1 (Gene)'
const ENCODES_ROW = 'Encodes: SYN-TF1 (Gene) to SYN-P1 (Protein (UniProtKB entry))'
const TF1_G2 = 'Transcriptional regulation: SYN-TF1 (Gene) to SYN-G2 (Gene)'
const CAPTION = 'Relationships in view'
const NETWORK_HEADING = 'Network'

const STATUS = '/api/graph/v1/status'
const NETWORK_TF1_ALL = '/api/graph/v1/network?seeds=entity:7&outcome=all'
const NETWORK_TF1_EXPAND = '/api/graph/v1/network?seeds=entity:7&expand=reference'
const RESOLVE_NS_TF1 = '/api/graph/v1/resolve?q=SYN-TF1&namespace=synthetic_gene'
const ENTITY_7 = '/api/graph/v1/entities/entity%3A7'
const ENTITY_7_BROWSER = '/api/graph/v1/entities/entity%3A7?browser=synthetic-regulation'
// Each page's first lazy render in this file waits for its modules to compile (12.0 item 6).
const FIRST_LAZY_WAIT = { timeout: 30_000 }
const ROUTES: [string, string, string, string][] = [
  ['/explore', EXPLORE_TITLE, EXPLORE_NOT_ENABLED, 'network'],
  ['/entities/entity:7', ENTITY_TITLE, ENTITY_NOT_ENABLED, 'entities'],
  ['/entities/synthetic_gene/SYN-TF1', ENTITY_LOOKUP_TITLE, ENTITY_LOOKUP_NOT_ENABLED, 'resolve'],
]

const asked = () => requests.urls.map((url) => url.pathname + url.search)
const statusOn = () => recorded<JsonBodyType>('status-on').body
const h1 = (name: string) => screen.getByRole('heading', { level: 1, name })
const rowNames = () => Array.from(within(screen.getByRole('region', { name: NETWORK_HEADING })).getByRole('table', { name: CAPTION })
  .querySelectorAll('tbody th button'), (node) => node.textContent)
const statusWith = (change: (body: GraphStatus) => JsonBodyType) =>
  server.use(http.get(STATUS, () => HttpResponse.json(change(recorded<GraphStatus>('status-on').body))))

/** BR/routes.test.tsx:98-111: a status handler that answers once the row releases it. */
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

function recordFocus() {
  const focused: Element[] = []
  const record = (event: FocusEvent) => {
    focused.push(event.target as Element)
  }
  document.addEventListener('focusin', record)
  return { focused, stop: () => document.removeEventListener('focusin', record) }
}

/** Read and parsed as XV-10 does: a module's import specifiers and whether each is type-only. */
function importsOf(path: string): [string, boolean][] {
  const name = `${ts.sys.getCurrentDirectory()}/${path}`
  const kind = path.endsWith('.tsx') ? ts.ScriptKind.TSX : ts.ScriptKind.TS
  const source = ts.createSourceFile(name, ts.sys.readFile(name) ?? '', ts.ScriptTarget.ES2020, true, kind)
  return source.statements.filter(ts.isImportDeclaration)
    .map((node): [string, boolean] => [(node.moduleSpecifier as ts.StringLiteral).text, node.importClause?.isTypeOnly ?? false])
}

describe("X1's typed routes", () => {
  it.each(ROUTES)("XT-1: an off portal shows each route's off sentence, asks only status and loads no chunk (%s)", async (path, title, off) => {
    server.use(...typedGraphOffHandlers)
    renderGraphRoute(path, { vocabularies: false })
    expect(await screen.findByText(off, {}, GRAPH_WAIT)).toBeInTheDocument()
    expect(h1(title)).toBeInTheDocument()
    expect(asked()).toEqual([STATUS])
    expect(loads).toEqual({ explore: 0, entities: 0 })
  }, 20_000)

  it.each(ROUTES)('XT-2: while status loads each route says so and loads no chunk (%s)', async (path, title, off) => {
    const status = heldStatus()
    const view = renderGraphRoute(path)
    expect(await screen.findByText(PAGE_LOADING, {}, GRAPH_WAIT)).toBeInTheDocument()
    expect(h1(title)).toBeInTheDocument()
    expect(screen.queryByText(off)).toBeNull()
    expect(asked()).toEqual([STATUS])
    expect(loads).toEqual({ explore: 0, entities: 0 })
    view.unmount()
    expect(loads).toEqual({ explore: 0, entities: 0 })
    status.release()
    await waitFor(() => expect(answers.answers).toContain(`200 ${STATUS}`), GRAPH_WAIT)
    expect(loads).toEqual({ explore: 0, entities: 0 })
  }, 20_000)

  it.each(ROUTES)('XT-3: an unknown status offers Try again and loads no chunk (%s)', async (path, title) => {
    server.use(http.get(STATUS, () => HttpResponse.html('<html></html>', { status: 500 })))
    renderGraphRoute(path)
    expect(await screen.findByText(PAGE_UNKNOWN, {}, GRAPH_WAIT)).toBeInTheDocument()
    expect(h1(title)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: TRY_AGAIN })).toBeInTheDocument()
    expect(asked()).toEqual([STATUS])
    expect(loads).toEqual({ explore: 0, entities: 0 })
  }, 20_000)

  it.each(ROUTES)('XT-4: each route reads its own key (%s)', async (path, _title, off, key) => {
    statusWith((body) => ({ ...body, features: Object.fromEntries(Object.keys(body.features).map((name) => [name, name !== key])) }))
    renderGraphRoute(path, { vocabularies: false })
    expect(await screen.findByText(off, {}, GRAPH_WAIT)).toBeInTheDocument()
    expect(asked()).toEqual([STATUS])
    expect(loads).toEqual({ explore: 0, entities: 0 })
  }, 20_000)

  it("XT-5: each chunk loads on its first visit behind its fallback, and the lookup's redirect shows none", async () => {
    const client = newGraphClient()
    client.setQueryData(graphStatusOptions().queryKey, recorded<GraphStatus>('status-on').body)
    const { router } = renderGraphRoute('/', { client })
    expect(screen.getByText(TEST_SHELL)).toBeInTheDocument()
    expect(asked()).toEqual([])
    expect(loads).toEqual({ explore: 0, entities: 0 })
    const focus = recordFocus()
    try {
      await act(() => router.navigate('/explore'))
      const fallback = screen.getByRole('status')
      expect(fallback.textContent).toBe(PAGE_LOADING)
      expect(h1(EXPLORE_TITLE).parentElement).toBe(fallback.parentElement)
      expect(await screen.findByRole('search', { name: SEARCH_FORM_NAME }, FIRST_LAZY_WAIT)).toBeInTheDocument()
      expect(screen.getByText(NO_SEED_PROMPT)).toBeInTheDocument()
      expect(loads).toEqual({ explore: 1, entities: 0 })
      await waitFor(() => expect(document.activeElement).toBe(h1(EXPLORE_TITLE)), GRAPH_WAIT)
      const explorer = h1(EXPLORE_TITLE)
      expect(asked()).toEqual([])
      await act(() => router.navigate('/entities/entity%3A7'))
      expect(h1(ENTITY_TITLE)).toBeInTheDocument()
      expect(screen.getByText(PAGE_LOADING)).toBeInTheDocument()
      const entity = await screen.findByRole('heading', { level: 1, name: TF1 }, FIRST_LAZY_WAIT)
      await waitFor(() => expect(document.activeElement).toBe(entity), GRAPH_WAIT)
      expect(loads).toEqual({ explore: 1, entities: 1 })
      expect(asked()).toEqual([ENTITY_7])
      const n = asked().length
      const main = document.querySelector('main') as HTMLElement
      const texts: string[] = []
      const observer = new MutationObserver(() => {
        texts.push(main.textContent ?? '')
      })
      observer.observe(main, { childList: true, characterData: true, subtree: true })
      try {
        await act(() => router.navigate('/entities/synthetic_gene/SYN-TF1'))
        await waitFor(() => expect(router.state.location.pathname).toBe('/entities/entity%3A7'), GRAPH_WAIT)
        expect(router.state.historyAction).toBe('REPLACE')
        const redirected = await screen.findByRole('heading', { level: 1, name: TF1 }, GRAPH_WAIT)
        await waitFor(() => expect(document.activeElement).toBe(redirected), GRAPH_WAIT)
        expect(loads).toEqual({ explore: 1, entities: 1 })
        expect(asked().slice(n)).toEqual([RESOLVE_NS_TF1])
        expect(texts.filter((text) => text.includes(PAGE_LOADING))).toEqual([])
        expect(focus.focused).toHaveLength(3)
        expect(focus.focused[0]).toBe(explorer)
        expect(focus.focused[1]).toBe(entity)
        expect(focus.focused[2]).toBe(redirected)
      } finally {
        observer.disconnect()
      }
    } finally {
      focus.stop()
    }
  }, 70_000)

  it("XT-6: deep links move no focus and do not scroll, but a lookup's redirect reads as an arrival", async () => {
    renderGraphRoute('/explore?v=1&seeds=entity%3A7')
    await waitFor(() => expect(rowNames()).toContain(ENCODES_ROW), GRAPH_WAIT)
    expect(document.activeElement).toBe(document.body)
    expect(scrolls).not.toHaveBeenCalled()
    expect(asked()).toEqual([STATUS, NETWORK_TF1_EXPAND])
    cleanup()
    let before = asked().length
    renderGraphRoute('/entities/entity:7')
    expect(await screen.findByRole('heading', { level: 1, name: TF1 }, GRAPH_WAIT)).toBeInTheDocument()
    await waitFor(() => expect(asked().slice(before)).toEqual([STATUS, ENTITY_7]), GRAPH_WAIT)
    expect(document.activeElement).toBe(document.body)
    expect(scrolls).not.toHaveBeenCalled()
    cleanup()
    before = asked().length
    renderGraphRoute('/entities/synthetic_gene/SYN-TF1')
    const heading = await screen.findByRole('heading', { level: 1, name: TF1 }, GRAPH_WAIT)
    await waitFor(() => expect(document.activeElement).toBe(heading), GRAPH_WAIT)
    expect(scrolls).toHaveBeenCalledTimes(1)
    expect(scrolls).toHaveBeenCalledWith(0, 0)
    expect(asked().slice(before)).toEqual([STATUS, RESOLVE_NS_TF1, ENTITY_7])
  }, 20_000)

  it("XT-7: a link outside main moves focus to the explorer's h1 and scrolls", async () => {
    const user = userEvent.setup()
    renderGraphRoute('/')
    const focus = recordFocus()
    try {
      const link = within(screen.getByRole('navigation')).getByRole('link', { name: SHELL_EXPLORE_LINK })
      await user.click(link)
      await screen.findByRole('search', { name: SEARCH_FORM_NAME }, GRAPH_WAIT)
      await waitFor(() => expect(document.activeElement).toBe(h1(EXPLORE_TITLE)), GRAPH_WAIT)
      // Compared one by one: the gate's loading h1 equals the explorer's under isEqualNode.
      expect(focus.focused).toHaveLength(2)
      expect(focus.focused[0]).toBe(link)
      expect(focus.focused[1]).toBe(h1(EXPLORE_TITLE))
      expect(scrolls).toHaveBeenCalledTimes(1)
      expect(scrolls).toHaveBeenCalledWith(0, 0)
      expect(asked()).toEqual([STATUS])
    } finally {
      focus.stop()
    }
  }, 20_000)

  it('XT-8: each route survives a reload with the same requests', async () => {
    const reload = async (path: string, check: () => Promise<void>, again?: string[]) => {
      const before = asked().length
      const first = renderGraphRoute(path)
      await check()
      const once = asked().slice(before)
      first.unmount()
      const middle = asked().length
      renderGraphRoute(createPath(first.router.state.location), { state: first.router.state.location.state, client: newGraphClient() })
      await check()
      expect(asked().slice(middle)).toEqual(again ?? once)
      cleanup()
      return { once, first }
    }
    const explore = await reload('/explore?v=1&seeds=entity%3A7&follow=off&outcome=all', async () => {
      await waitFor(() => expect(rowNames()).toContain(TF1_G2), GRAPH_WAIT)
      expect(screen.getByRole('checkbox', { name: FOLLOW_LABEL })).not.toBeChecked()
      expect(screen.getByRole('checkbox', { name: OUTCOME_LABEL })).toBeChecked()
    })
    expect(explore.once).toEqual([STATUS, NETWORK_TF1_ALL])
    const entity = await reload('/entities/entity%3A7?browser=synthetic-regulation', async () => {
      expect(await screen.findByRole('link', { name: EXPLORE_ENTITY }, GRAPH_WAIT))
        .toHaveAttribute('href', '/explore?v=1&seeds=entity%3A7&browser=synthetic-regulation')
    })
    expect(entity.once).toEqual([STATUS, ENTITY_7_BROWSER])
    const lookup = await reload('/entities/synthetic_gene/SYN-TF1', async () => {
      expect(await screen.findByRole('heading', { level: 1, name: TF1 }, GRAPH_WAIT)).toBeInTheDocument()
      await waitFor(() => expect(answers.answers.filter((answer) => answer === `200 ${ENTITY_7}`).length).toBeGreaterThan(0), GRAPH_WAIT)
    }, [STATUS, ENTITY_7])
    expect(lookup.once).toEqual([STATUS, RESOLVE_NS_TF1, ENTITY_7])
    expect(lookup.first.router.state.location.pathname).toBe('/entities/entity%3A7')
  }, 20_000)

  it('XT-9: under a basename every link carries it', async () => {
    let before = asked().length
    renderGraphRoute('/openpip/explore?v=1&seeds=entity%3A7', { basename: '/openpip/' })
    await waitFor(() => expect(answers.answers).toContain(`200 ${NETWORK_TF1_EXPAND}`), GRAPH_WAIT)
    expect(asked().slice(before)).toEqual([STATUS, NETWORK_TF1_EXPAND])
    expect(within(screen.getByRole('list', { name: CHOSEN_LABEL })).getAllByRole('listitem')).toHaveLength(1)
    cleanup()
    renderGraphRoute('/openpip/explore?v=2', { basename: '/openpip/' })
    expect(await screen.findByRole('link', { name: NEWER_VERSION_LINK }, GRAPH_WAIT)).toHaveAttribute('href', '/openpip/explore')
    cleanup()
    renderGraphRoute('/openpip/entities/entity:7', { basename: '/openpip/' })
    expect(await screen.findByRole('link', { name: EXPLORE_ENTITY }, GRAPH_WAIT)).toHaveAttribute('href', '/openpip/explore?v=1&seeds=entity%3A7')
    cleanup()
    before = asked().length
    renderGraphRoute('/openpip/entities/synthetic_gene/syn-tf1', { basename: '/openpip/' })
    expect(await screen.findByRole('link', { name: openText(TF1) }, GRAPH_WAIT)).toHaveAttribute('href', '/openpip/entities/entity%3A7')
    expect(asked().slice(before)).toEqual([STATUS, '/api/graph/v1/resolve?q=syn-tf1&namespace=synthetic_gene'])
  }, 20_000)

  it("XT-10: a path no route matches keeps React Router's default page", async () => {
    for (const path of ['/explore/x', '/entities', '/entities/a/b/c']) {
      const view = renderGraphRoute(path)
      expect(await screen.findByText(NO_ROUTE, {}, GRAPH_WAIT)).toBeInTheDocument()
      for (const title of [EXPLORE_TITLE, ENTITY_TITLE, ENTITY_LOOKUP_TITLE]) expect(screen.queryByRole('heading', { level: 1, name: title })).toBeNull()
      for (const words of [EXPLORE_NOT_ENABLED, ENTITY_NOT_ENABLED, ENTITY_LOOKUP_NOT_ENABLED, PAGE_LOADING, PAGE_UNKNOWN]) {
        expect(screen.queryByText(words)).toBeNull()
      }
      expect(asked(), path).toEqual([])
      view.unmount()
      // The allowance (12.0; F4's RT-9): React Router's one call, asserted here and cleared, so any other call fails.
      const errors = vi.mocked(console.error).mock.calls.map((args) => String(args[0]))
      expect(errors, path).toHaveLength(1)
      expect(errors[0].startsWith('Error handled by React Router default ErrorBoundary:')).toBe(true)
      vi.mocked(console.error).mockClear()
    }
  }, 20_000)

  it('XT-11: paths match without regard to case or a trailing slash', async () => {
    for (const path of ['/Explore?v=1&seeds=entity%3A7', '/explore/?v=1&seeds=entity%3A7']) {
      const before = asked().length
      const { router } = renderGraphRoute(path)
      expect(await screen.findByRole('heading', { level: 1, name: EXPLORE_TITLE }, GRAPH_WAIT)).toBeInTheDocument()
      await waitFor(() => expect(within(screen.getByRole('list', { name: CHOSEN_LABEL })).getAllByRole('listitem')[0].textContent).toContain(TF1), GRAPH_WAIT)
      expect(asked().slice(before)).toEqual([STATUS, NETWORK_TF1_EXPAND])
      expect(router.state.location.pathname).toBe(path.split('?')[0])
      cleanup()
    }
    for (const path of ['/Entities/entity:7', '/entities/entity:7/']) {
      const before = asked().length
      renderGraphRoute(path)
      expect(await screen.findByRole('heading', { level: 1, name: TF1 }, GRAPH_WAIT)).toBeInTheDocument()
      await waitFor(() => expect(asked().slice(before)).toEqual([STATUS, ENTITY_7]), GRAPH_WAIT)
      cleanup()
    }
  }, 20_000)

  it("XT-12: the route table holds X1's three objects with F4's boundary and no loader", () => {
    expect(TYPED_ROUTES.map((route) => [route.path, route.Component, route.ErrorBoundary])).toEqual([
      ['browsers', BrowsersGalleryRoute, TypedRouteError], ['browsers/:slug', BrowserInstanceRoute, TypedRouteError],
      ['explore', ExploreRoute, TypedRouteError], ['entities/:id', EntityRoute, TypedRouteError],
      ['entities/:namespace/:value', EntityLookupRoute, TypedRouteError],
    ])
    for (const route of TYPED_ROUTES) {
      for (const key of ['lazy', 'loader', 'element', 'HydrateFallback']) expect(Object.keys(route), String(route.path)).not.toContain(key)
    }
  })

  it('XT-13: the route modules import the first-load set and nothing more', () => {
    const firstLoad = ['react', '../graph/pages/pageWords', '../graph/pages/TypedFeatureGate', '../graph/pages/TypedRouteStates']
    expect(importsOf('src/features/explore/ExploreRoutes.tsx').map(([name]) => name)).toEqual(firstLoad)
    expect(importsOf('src/features/entities/EntitiesRoutes.tsx').map(([name]) => name)).toEqual(firstLoad)
    expect(importsOf('src/features/browsers/BrowsersRoutes.tsx').map(([name]) => name)).toEqual(firstLoad)
    const routes = importsOf('src/features/graph/pages/typedRoutes.ts')
    expect(routes.map(([name]) => name))
      .toEqual(['react-router-dom', '../../browsers/BrowsersRoutes', '../../entities/EntitiesRoutes', '../../explore/ExploreRoutes', './TypedRouteStates'])
    expect(routes[0][1]).toBe(true)
  })
})
