import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import type { QueryClient } from '@tanstack/react-query'
import { http, HttpResponse } from 'msw'
import type { JsonBodyType } from 'msw'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { graphStatusOptions } from '../../api/graph/status'
import { Navbar } from '../../components/Navbar'
import { typedGraphOffHandlers } from '../../mocks/handlers/typedGraph'
import { server } from '../../mocks/server'
import { useAuthStore } from '../../store/authStore'
import { newGraphClient, recordRequests, recorded } from '../../test/graphHooks'
import type { RequestLog } from '../../test/graphHooks'
import { recordAnswers } from '../../test/graphRoutes'
import type { GraphStatus } from '../graph/types'

let requests: RequestLog
let answers: { answers: string[]; stop: () => void }

beforeEach(() => {
  useAuthStore.getState().logout()
  requests = recordRequests()
  answers = recordAnswers()
  vi.spyOn(console, 'warn')
  vi.spyOn(console, 'error')
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

// Words copied from the spec's section 3.1 (BROWSERS_NAV_LABEL), never from the module under test.
const LABEL = 'Browsers'
// X1's words, copied from its spec's section 11.4 (EXPLORE_NAV_LABEL), never from the module under test.
const EXPLORE = 'Explore'
// main's Navbar with the switch off, logged out, written by hand (Navbar.tsx and the nav text group's defaults).
const NAV_1 = [
  'Home /', 'Search /search', 'Proteins /proteins', 'Downloads /download', 'API /developer', 'About /about', 'FAQ /faq', 'Contact /contact',
  'Login /login', 'Register /register',
]
const STATUS = '/api/graph/v1/status'

/** The unchanged Navbar, as components/Navbar.test.tsx renders it; the client is returned for the settled wait. */
function renderNavbar(isLoggedIn: boolean, path = '/'): QueryClient {
  const client = newGraphClient()
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <Navbar isLoggedIn={isLoggedIn} />
      </MemoryRouter>
    </QueryClientProvider>,
  )
  return client
}
const links = () => screen.getAllByRole('link').map((link) => `${link.textContent} ${link.getAttribute('href')}`)
// The typed requests only: the Navbar reads status once when it loads (J6); its legacy text request is not F4's to pin.
const typedAsked = () => requests.urls.map((url) => url.pathname + url.search).filter((path) => path.startsWith('/api/graph/'))
// Every state starts as loading, so an absence is checked only once status has answered.
const settled = (client: QueryClient) =>
  waitFor(() => expect(client.getQueryState(graphStatusOptions().queryKey)?.status).not.toBe('pending'), { timeout: 10_000 })

describe("the Navbar's Browsers link", () => {
  it('NAV-1: an off portal, logged out, shows the legacy links only', async () => {
    server.use(...typedGraphOffHandlers)
    const client = renderNavbar(false)
    await settled(client)
    expect(links()).toMatchInlineSnapshot(`
      [
        "Home /",
        "Search /search",
        "Proteins /proteins",
        "Downloads /download",
        "API /developer",
        "About /about",
        "FAQ /faq",
        "Contact /contact",
        "Login /login",
        "Register /register",
      ]
    `)
    expect(typedAsked()).toEqual([STATUS])
  }, 20_000)

  it('NAV-2: an off portal, logged in, shows the legacy links and Profile', async () => {
    server.use(...typedGraphOffHandlers)
    const client = renderNavbar(true)
    await settled(client)
    expect(links()).toMatchInlineSnapshot(`
      [
        "Home /",
        "Search /search",
        "Proteins /proteins",
        "Downloads /download",
        "API /developer",
        "About /about",
        "FAQ /faq",
        "Contact /contact",
        "Profile /profile",
      ]
    `)
    expect(typedAsked()).toEqual([STATUS])
  }, 20_000)

  it('NAV-3: with the switch on, Browsers follows Contact and Explore follows Browsers, the other links keep their order, and Browsers is current on an instance', async () => {
    renderNavbar(false)
    expect(await screen.findByRole('link', { name: LABEL }, { timeout: 10_000 })).toBeInTheDocument()
    expect(await screen.findByRole('link', { name: EXPLORE }, { timeout: 10_000 })).toBeInTheDocument()
    const shown = links()
    // The first ten and the last two, so that the typed link C1 adds after Explore keeps the row passing.
    expect(shown.slice(0, 10)).toEqual([...NAV_1.slice(0, 8), 'Browsers /browsers', 'Explore /explore'])
    expect(shown.slice(-2)).toEqual(['Login /login', 'Register /register'])
    cleanup()
    renderNavbar(false, '/browsers/synthetic-regulation')
    expect(await screen.findByRole('link', { name: LABEL }, { timeout: 10_000 })).toHaveAttribute('aria-current', 'page')
    // One read per load: each render has a client of its own.
    expect(typedAsked()).toEqual([STATUS, STATUS])
  }, 20_000)

  it('NAV-3b: with resolve, entities, network and relationships on, browsers off shows Explore and no Browsers link', async () => {
    const body = recorded<GraphStatus>('status-on').body
    server.use(http.get(STATUS, () => HttpResponse.json({ ...body, features: { ...body.features, browsers: false } })))
    renderNavbar(false)
    expect(await screen.findByRole('link', { name: EXPLORE }, { timeout: 10_000 })).toHaveAttribute('href', '/explore')
    expect(screen.queryByRole('link', { name: LABEL })).toBeNull()
    expect(links()).toEqual([...NAV_1.slice(0, 8), 'Explore /explore', 'Login /login', 'Register /register'])
    expect(typedAsked()).toEqual([STATUS])
  }, 20_000)

  it('NAV-3c: with resolve, entities, browsers and relationships on, network off shows Browsers and no Explore link', async () => {
    const body = recorded<GraphStatus>('status-on').body
    server.use(http.get(STATUS, () => HttpResponse.json({ ...body, features: { ...body.features, network: false } })))
    renderNavbar(false)
    expect(await screen.findByRole('link', { name: LABEL }, { timeout: 10_000 })).toHaveAttribute('href', '/browsers')
    expect(screen.queryByRole('link', { name: EXPLORE })).toBeNull()
    expect(links()).toEqual([...NAV_1.slice(0, 8), 'Browsers /browsers', 'Login /login', 'Register /register'])
    expect(typedAsked()).toEqual([STATUS])
  }, 20_000)

  it('NAV-4: an unknown status shows no Browsers link', async () => {
    server.use(http.get(STATUS, () => HttpResponse.html('<html></html>', { status: 500 })))
    const client = renderNavbar(false)
    await settled(client)
    expect(client.getQueryState(graphStatusOptions().queryKey)?.status).toBe('error')
    expect(screen.queryByRole('link', { name: LABEL })).toBeNull()
    expect(links()).toEqual(NAV_1)
    expect(typedAsked()).toEqual([STATUS])
  }, 20_000)

  it('NAV-5: no Browsers link while status loads, nor once it says off', async () => {
    let release: () => void = () => {}
    const held = new Promise<void>((resolve) => {
      release = resolve
    })
    server.use(http.get(STATUS, async () => {
      await held
      return HttpResponse.json(recorded<JsonBodyType>('status-off').body)
    }))
    const client = renderNavbar(false)
    await waitFor(() => expect(requests.urls.map((url) => url.pathname)).toContain(STATUS), { timeout: 10_000 })
    expect(client.getQueryState(graphStatusOptions().queryKey)?.status).toBe('pending')
    expect(screen.queryByRole('link', { name: LABEL })).toBeNull()
    release()
    await settled(client)
    expect(screen.queryByRole('link', { name: LABEL })).toBeNull()
    expect(typedAsked()).toEqual([STATUS])
  }, 20_000)
})

describe("the Navbar's Explore link", () => {
  it('XA-1: no Explore link while status loads, nor once it says off', async () => {
    let release: () => void = () => {}
    const held = new Promise<void>((resolve) => {
      release = resolve
    })
    server.use(http.get(STATUS, async () => {
      await held
      return HttpResponse.json(recorded<JsonBodyType>('status-off').body)
    }))
    const client = renderNavbar(false)
    await waitFor(() => expect(requests.urls.map((url) => url.pathname)).toContain(STATUS), { timeout: 10_000 })
    expect(client.getQueryState(graphStatusOptions().queryKey)?.status).toBe('pending')
    expect(screen.queryByRole('link', { name: EXPLORE })).toBeNull()
    release()
    await settled(client)
    expect(screen.queryByRole('link', { name: EXPLORE })).toBeNull()
    expect(links()).toEqual(NAV_1)
    expect(typedAsked()).toEqual([STATUS])
  }, 20_000)

  it('XA-2: Explore is current on the explorer without regard to case, and no link is current on an entity page', async () => {
    renderNavbar(false, '/explore?v=1&seeds=entity%3A7')
    expect(await screen.findByRole('link', { name: EXPLORE }, { timeout: 10_000 })).toHaveAttribute('aria-current', 'page')
    expect(screen.getByRole('link', { name: LABEL })).not.toHaveAttribute('aria-current')
    cleanup()
    renderNavbar(false, '/Explore')
    expect(await screen.findByRole('link', { name: EXPLORE }, { timeout: 10_000 })).toHaveAttribute('aria-current', 'page')
    cleanup()
    renderNavbar(false, '/entities/entity:7')
    expect(await screen.findByRole('link', { name: EXPLORE }, { timeout: 10_000 })).toBeInTheDocument()
    expect(screen.getAllByRole('link').filter((link) => link.hasAttribute('aria-current'))).toEqual([])
    // One read per load: each render has a client of its own.
    expect(typedAsked()).toEqual([STATUS, STATUS, STATUS])
  }, 20_000)

  it('XA-3: under a basename the Explore link carries it', async () => {
    render(
      <QueryClientProvider client={newGraphClient()}>
        <MemoryRouter basename="/openpip" initialEntries={['/openpip/']}>
          <Navbar isLoggedIn={false} />
        </MemoryRouter>
      </QueryClientProvider>,
    )
    expect(await screen.findByRole('link', { name: EXPLORE }, { timeout: 10_000 })).toHaveAttribute('href', '/openpip/explore')
    expect(screen.getByRole('link', { name: LABEL })).toHaveAttribute('href', '/openpip/browsers')
    expect(typedAsked()).toEqual([STATUS])
  }, 20_000)
})
