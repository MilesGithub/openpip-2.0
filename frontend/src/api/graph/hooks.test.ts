import { act, renderHook, waitFor } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import type { JsonBodyType } from 'msw'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import type { GraphBrowsersResponse } from '../../features/graph/types'
import { demoRefs } from '../../mocks/fixtures/typedGraph/recorded'
import { server } from '../../mocks/server'
import { useAuthStore } from '../../store/authStore'
import { GRAPH_WAIT, graphWrapper, newGraphClient, recorded, recordRequests } from '../../test/graphHooks'
import type { RequestLog } from '../../test/graphHooks'
import { useGraphBrowser, useGraphBrowsers } from './browsers'
import { useGraphEntity } from './entities'
import { classifyGraphError } from './errors'
import { useGraphNetwork } from './network'
import { useGraphEvidence, useGraphRelationship } from './relationships'
import { useGraphResolve } from './resolve'
import { graphStatusOptions, useGraphStatus } from './status'
import { graphVocabulariesOptions, useGraphVocabularies } from './vocabularies'

const { tf1, tf2, tf3, p1, p2, v1, v2, forward, mixed, testedAbsent } = demoRefs

const ANY_TYPED = '/api/graph/v1/*'
const NETWORK = '/api/graph/v1/network'
const RESOLVE = '/api/graph/v1/resolve'
const ENTITY = '/api/graph/v1/entities/:id'
const RELATIONSHIP = '/api/graph/v1/relationships/:ref'
const EVIDENCE = '/api/graph/v1/relationships/:ref/evidence'
const BROWSERS = '/api/graph/v1/browsers'

const encoded = (ref: string) => ref.replace(':', '%3A')
const seeded = { seeds: [tf1] }
const adminToken = `h.${btoa(JSON.stringify({ is_admin: true }))}.s`
const plainToken = `h.${btoa(JSON.stringify({}))}.s`

const recordedBody = (name: string) => recorded<JsonBodyType>(name).body
const jsonNetwork = () => HttpResponse.json(recordedBody('network-tf1'))
const json503 = () => HttpResponse.json({ detail: 'Too expensive.' }, { status: 503 })

function pause(milliseconds: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, milliseconds))
}

function counting(path: string, reply: () => Response): { calls: number } {
  const counter = { calls: 0 }
  server.use(
    http.get(path, () => {
      counter.calls += 1
      return reply()
    }),
  )
  return counter
}

let log: RequestLog

beforeEach(() => {
  useAuthStore.getState().logout()
  log = recordRequests()
})

afterEach(() => {
  log.stop()
  expect(log.unhandled).toEqual([])
})

describe('a round trip', () => {
  it.each([
    { useCase: () => useGraphStatus(), pathname: '/api/graph/v1/status', search: '', entry: 'status-on' },
    { useCase: () => useGraphVocabularies(), pathname: '/api/graph/v1/vocabularies', search: '', entry: 'vocabularies' },
    {
      useCase: () => useGraphResolve({ q: ['SYN-TF1', 'SYN-SHARED'] }),
      pathname: '/api/graph/v1/resolve',
      search: '?q=SYN-TF1,SYN-SHARED',
      entry: 'resolve-tf1-shared',
    },
    {
      useCase: () => useGraphResolve({ q: ['SYN-NOPE'] }),
      pathname: '/api/graph/v1/resolve',
      search: '?q=SYN-NOPE',
      entry: 'resolve-unmatched',
    },
    {
      useCase: () => useGraphEntity(tf1),
      pathname: `/api/graph/v1/entities/${encoded(tf1)}`,
      search: '',
      entry: 'entity-tf1',
    },
    { useCase: () => useGraphNetwork(seeded), pathname: NETWORK, search: `?seeds=${tf1}`, entry: 'network-tf1' },
    {
      useCase: () => useGraphNetwork({ ...seeded, outcome: 'all' }),
      pathname: NETWORK,
      search: `?seeds=${tf1}&outcome=all`,
      entry: 'network-tf1-outcome-all',
    },
    {
      useCase: () => useGraphNetwork({ ...seeded, releases: [v2, v1] }),
      pathname: NETWORK,
      search: `?seeds=${tf1}&releases=${v1},${v2}`,
      entry: 'network-tf1-both-releases',
    },
    {
      useCase: () => useGraphNetwork({ seeds: [tf3, tf1, p2, tf2], outcome: 'all' }),
      pathname: NETWORK,
      search: `?seeds=${p2},${tf1},${tf2},${tf3}&outcome=all`,
      entry: 'network-all-edges',
    },
    {
      useCase: () => useGraphNetwork({ ...seeded, browser: 'synthetic-regulation' }),
      pathname: NETWORK,
      search: `?seeds=${tf1}&browser=synthetic-regulation`,
      entry: 'network-tf1-browser',
    },
    {
      useCase: () => useGraphRelationship(mixed),
      pathname: `/api/graph/v1/relationships/${encoded(mixed)}`,
      search: '',
      entry: 'relationship-mixed',
    },
    {
      useCase: () => useGraphRelationship(forward, { releases: [v1] }),
      pathname: `/api/graph/v1/relationships/${encoded(forward)}`,
      search: `?releases=${v1}`,
      entry: 'relationship-effect-v1',
    },
    { useCase: () => useGraphBrowsers(), pathname: BROWSERS, search: '', entry: 'browsers' },
    {
      useCase: () => useGraphBrowser('synthetic-regulation'),
      pathname: '/api/graph/v1/browsers/synthetic-regulation',
      search: '',
      entry: 'browser-typed',
    },
  ])('$entry', async ({ useCase, pathname, search, entry }) => {
    const { result } = renderHook(() => useCase(), { wrapper: graphWrapper() })
    await waitFor(() => expect(result.current.isSuccess).toBe(true), GRAPH_WAIT)
    expect(result.current.data).toEqual(recorded(entry).body)
    expect(log.urls[0].pathname).toBe(pathname)
    expect(log.urls[0].search).toBe(search)
  })
})

describe('a disabled hook', () => {
  it.each([
    { name: 'seeds []', useCase: () => useGraphNetwork({ seeds: [] }) },
    { name: 'layers []', useCase: () => useGraphNetwork({ ...seeded, layers: [] }) },
    { name: 'releases []', useCase: () => useGraphNetwork({ ...seeded, releases: [] }) },
    { name: 'outcome []', useCase: () => useGraphNetwork({ ...seeded, outcome: [] }) },
    { name: 'q []', useCase: () => useGraphResolve({ q: [] }) },
    { name: 'an empty entity id', useCase: () => useGraphEntity('') },
    { name: 'an empty relationship ref', useCase: () => useGraphEvidence('') },
    { name: 'an empty slug', useCase: () => useGraphBrowser('') },
    { name: 'a null entity id', useCase: () => useGraphEntity(null) },
    { name: 'a null relationship ref', useCase: () => useGraphRelationship(null) },
    { name: 'a null evidence ref', useCase: () => useGraphEvidence(null) },
    { name: 'a null slug', useCase: () => useGraphBrowser(null) },
    { name: 'a seed list holding only null', useCase: () => useGraphNetwork({ seeds: [null] }) },
    { name: 'a term list holding only null', useCase: () => useGraphResolve({ q: [null] }) },
  ])('$name sends nothing', async ({ useCase }) => {
    const { result } = renderHook(() => useCase(), { wrapper: graphWrapper() })
    expect(result.current.fetchStatus).toBe('idle')
    await pause(30)
    expect(log.urls).toEqual([])
  })
})

const keyedHooks = [
  { name: 'useGraphResolve', prefix: ['graph', 'resolve'], useCase: () => useGraphResolve({ q: ['SYN-NOPE'] }) },
  { name: 'useGraphEntity', prefix: ['graph', 'entity', tf1], useCase: () => useGraphEntity(tf1) },
  { name: 'useGraphNetwork', prefix: ['graph', 'network'], useCase: () => useGraphNetwork(seeded) },
  { name: 'useGraphRelationship', prefix: ['graph', 'relationship', mixed, 'page'], useCase: () => useGraphRelationship(mixed) },
  { name: 'useGraphEvidence', prefix: ['graph', 'relationship', mixed, 'evidence'], useCase: () => useGraphEvidence(mixed) },
  { name: 'useGraphBrowsers', prefix: ['graph', 'browsers', 'list'], useCase: () => useGraphBrowsers() },
  {
    name: 'useGraphBrowser',
    prefix: ['graph', 'browsers', 'detail', 'synthetic-regulation'],
    useCase: () => useGraphBrowser('synthetic-regulation'),
  },
]

describe('the query key', () => {
  it.each(keyedHooks)('$name sits under graph and moves with the caller', async ({ prefix, useCase }) => {
    const client = newGraphClient()
    const active = (viewer: string) =>
      client.getQueryCache().findAll({ queryKey: [...prefix, viewer], type: 'active' })
    const expectActive = async (viewer: string, left?: string) => {
      await waitFor(() => expect(active(viewer)).toHaveLength(1), GRAPH_WAIT)
      if (left !== undefined) expect(active(left)).toHaveLength(0)
    }

    useAuthStore.getState().login(adminToken, 'r', true)
    renderHook(() => useCase(), { wrapper: graphWrapper(client) })
    await expectActive('staff')

    act(() => useAuthStore.getState().logout())
    await expectActive('visitor', 'staff')

    act(() => useAuthStore.getState().login(plainToken, 'r', false))
    await expectActive('account', 'visitor')

    // Another tab's sign-in reaches this tab's localStorage, and never its store.
    act(() => {
      localStorage.setItem('openpip_access_token', adminToken)
      window.dispatchEvent(new StorageEvent('storage'))
    })
    await expectActive('staff', 'account')

    // Sent all the same (client.ts:25), so it is never the logged-out entry.
    act(() => {
      localStorage.setItem('openpip_access_token', 'h.@@@.s')
      window.dispatchEvent(new StorageEvent('storage'))
    })
    await expectActive('account', 'staff')
  })

  it('a page restored from the back/forward cache reads the token again', async () => {
    const client = newGraphClient()
    const active = (viewer: string) =>
      client.getQueryCache().findAll({ queryKey: ['graph', 'network', viewer], type: 'active' })
    const { result } = renderHook(() => useGraphNetwork(seeded), { wrapper: graphWrapper(client) })
    // Settled first: an answer still in flight would re-render, and hide a missing listener.
    await waitFor(() => expect(result.current.isSuccess).toBe(true), GRAPH_WAIT)
    expect(active('visitor')).toHaveLength(1)

    // Another tab's sign-in, whose storage event never reached a frozen page.
    act(() => {
      localStorage.setItem('openpip_access_token', adminToken)
      window.dispatchEvent(new Event('pageshow'))
    })
    await waitFor(() => expect(active('staff')).toHaveLength(1), GRAPH_WAIT)
    expect(active('visitor')).toHaveLength(0)
  })

  it('holds no caller on status and vocabularies', () => {
    expect(graphStatusOptions().queryKey).toEqual(['graph', 'status'])
    expect(graphVocabulariesOptions().queryKey).toEqual(['graph', 'vocabularies'])
  })
})

interface PassedOptions {
  enabled?: boolean
  retry?: boolean
}

const hooksTakingOptions = [
  { name: 'useGraphStatus', useCase: (options: PassedOptions) => useGraphStatus(options) },
  { name: 'useGraphVocabularies', useCase: (options: PassedOptions) => useGraphVocabularies(options) },
  { name: 'useGraphResolve', useCase: (options: PassedOptions) => useGraphResolve({ q: ['SYN-NOPE'] }, options) },
  { name: 'useGraphEntity', useCase: (options: PassedOptions) => useGraphEntity(tf1, {}, options) },
  { name: 'useGraphNetwork', useCase: (options: PassedOptions) => useGraphNetwork(seeded, options) },
  { name: 'useGraphRelationship', useCase: (options: PassedOptions) => useGraphRelationship(mixed, {}, options) },
  { name: 'useGraphEvidence', useCase: (options: PassedOptions) => useGraphEvidence(mixed, {}, options) },
  { name: 'useGraphBrowsers', useCase: (options: PassedOptions) => useGraphBrowsers({}, options) },
  { name: 'useGraphBrowser', useCase: (options: PassedOptions) => useGraphBrowser('synthetic-regulation', options) },
]

describe("a caller's options", () => {
  it.each(hooksTakingOptions)('$name honours enabled: false', async ({ useCase }) => {
    const { result } = renderHook(() => useCase({ enabled: false }), { wrapper: graphWrapper() })
    expect(result.current.fetchStatus).toBe('idle')
    await pause(30)
    expect(log.urls).toEqual([])
  })

  it.each(hooksTakingOptions)('$name honours retry: false', async ({ useCase }) => {
    const counter = counting(ANY_TYPED, () => HttpResponse.html('<html>', { status: 502 }))
    const { result } = renderHook(() => useCase({ retry: false }), { wrapper: graphWrapper() })
    await waitFor(() => expect(result.current.isError).toBe(true), GRAPH_WAIT)
    expect(counter.calls).toBe(1)
  })

  it.each(hooksTakingOptions)('$name reads an explicit retry: undefined as its own predicate', async ({ useCase }) => {
    const counter = counting(ANY_TYPED, json503)
    const { result } = renderHook(() => useCase({ retry: undefined }), { wrapper: graphWrapper() })
    await waitFor(() => expect(result.current.isError).toBe(true), GRAPH_WAIT)
    expect(counter.calls).toBe(1)
  })

  it.each([
    {
      name: 'useGraphResolve',
      useCase: (changed: boolean) =>
        useGraphResolve({ q: changed ? ['SYN-TF1', 'SYN-SHARED'] : ['SYN-NOPE'] }, { keepPrevious: true }),
    },
    { name: 'useGraphEntity', useCase: (changed: boolean) => useGraphEntity(changed ? p1 : tf1, {}, { keepPrevious: true }) },
    {
      name: 'useGraphNetwork',
      useCase: (changed: boolean) =>
        useGraphNetwork({ ...seeded, outcome: changed ? 'all' : undefined }, { keepPrevious: true }),
    },
    {
      name: 'useGraphRelationship',
      useCase: (changed: boolean) => useGraphRelationship(changed ? testedAbsent : mixed, {}, { keepPrevious: true }),
    },
    {
      name: 'useGraphEvidence',
      useCase: (changed: boolean) =>
        useGraphEvidence(mixed, { page_size: changed ? 1 : undefined }, { keepPrevious: true }),
    },
    {
      name: 'useGraphBrowsers',
      useCase: (changed: boolean) =>
        useGraphBrowsers({ tag: changed ? 'gene_regulation' : undefined }, { keepPrevious: true }),
    },
    {
      name: 'useGraphBrowser',
      useCase: (changed: boolean) =>
        useGraphBrowser(changed ? 'protein-interactions' : 'synthetic-regulation', { keepPrevious: true }),
    },
  ])('$name keeps the last body while a filter changes', async ({ useCase }) => {
    server.use(http.get(BROWSERS, () => HttpResponse.json(recordedBody('browsers'))))
    const { result, rerender } = renderHook(({ changed }) => useCase(changed), {
      wrapper: graphWrapper(),
      initialProps: { changed: false },
    })
    await waitFor(() => expect(result.current.isSuccess).toBe(true), GRAPH_WAIT)
    const first = result.current.data
    rerender({ changed: true })
    expect(result.current.isPlaceholderData).toBe(true)
    expect(result.current.data).toBe(first)
    await waitFor(() => expect(result.current.isPlaceholderData).toBe(false), GRAPH_WAIT)
  })

  it("drops staff's kept body at a logout", async () => {
    let calls = 0
    let release: () => void = () => {}
    const held = new Promise<void>((resolve) => {
      release = resolve
    })
    server.use(
      http.get(NETWORK, async () => {
        calls += 1
        if (calls > 1) await held
        return jsonNetwork()
      }),
    )
    useAuthStore.getState().login(adminToken, 'r', true)
    const { result, rerender } = renderHook(
      ({ outcome }: { outcome?: 'all' }) => useGraphNetwork({ ...seeded, outcome }, { keepPrevious: true }),
      { wrapper: graphWrapper(), initialProps: {} as { outcome?: 'all' } },
    )
    await waitFor(() => expect(result.current.isSuccess).toBe(true), GRAPH_WAIT)
    const first = result.current.data
    rerender({ outcome: 'all' })
    expect(result.current.isPlaceholderData).toBe(true)
    expect(result.current.data).toBe(first)

    act(() => useAuthStore.getState().logout())
    await waitFor(() => expect(result.current.data).toBeUndefined(), GRAPH_WAIT)
    expect(result.current.isPending).toBe(true)

    release()
    await waitFor(() => expect(result.current.isSuccess).toBe(true), GRAPH_WAIT)
  })
})

describe('what is not repeated', () => {
  it('a JSON 503 is not retried', async () => {
    const counter = counting(NETWORK, json503)
    const { result } = renderHook(() => useGraphNetwork(seeded), { wrapper: graphWrapper() })
    await waitFor(() => expect(result.current.isError).toBe(true), GRAPH_WAIT)
    expect(counter.calls).toBe(1)
    expect(classifyGraphError(result.current.error).kind).toBe('too_expensive')
  })

  it('a stored 503 is not sent again by a remount', async () => {
    const counter = counting(NETWORK, json503)
    const wrapper = graphWrapper()
    const first = renderHook(() => useGraphNetwork(seeded), { wrapper })
    await waitFor(() => expect(first.result.current.isError).toBe(true), GRAPH_WAIT)
    expect(counter.calls).toBe(1)
    first.unmount()

    const second = renderHook(() => useGraphNetwork(seeded), { wrapper })
    expect(second.result.current.fetchStatus).toBe('idle')
    await pause(50)
    expect(counter.calls).toBe(1)

    await act(() => second.result.current.refetch())
    expect(counter.calls).toBe(2)
  })

  it('a failed refetch is not re-sent by a remount', async () => {
    let answer: () => Response = jsonNetwork
    const counter = counting(NETWORK, () => answer())
    const wrapper = graphWrapper()
    const first = renderHook(() => useGraphNetwork(seeded), { wrapper })
    await waitFor(() => expect(first.result.current.isSuccess).toBe(true), GRAPH_WAIT)

    answer = json503
    await act(() => first.result.current.refetch())
    await waitFor(() => expect(first.result.current.isRefetchError).toBe(true), GRAPH_WAIT)
    expect(counter.calls).toBe(2)
    first.unmount()

    const second = renderHook(() => useGraphNetwork(seeded), { wrapper })
    expect(second.result.current.fetchStatus).toBe('idle')
    await pause(50)
    expect(counter.calls).toBe(2)

    await act(() => second.result.current.refetch())
    expect(counter.calls).toBe(3)
  })
})

describe('what reaches the wire', () => {
  it.each([
    {
      name: 'an entity browser',
      useCase: () => useGraphEntity(tf1, { browser: 'synthetic-regulation' }),
      search: '?browser=synthetic-regulation',
    },
    {
      name: 'an entity release list',
      useCase: () => useGraphEntity(tf1, { releases: [v2, v1] }),
      search: `?releases=${v1},${v2}`,
    },
    {
      name: 'a gallery filter',
      useCase: () => useGraphBrowsers({ tag: 'gene_regulation' }),
      search: '?tag=gene_regulation',
    },
    {
      name: 'a resolve browser',
      useCase: () => useGraphResolve({ q: ['SYN-TF1'], browser: 'synthetic-regulation' }),
      search: '?q=SYN-TF1&browser=synthetic-regulation',
    },
    {
      name: 'a resolve taxon',
      useCase: () => useGraphResolve({ q: ['SYN-TF1'], taxon: 32630 }),
      search: '?q=SYN-TF1&taxon=32630',
    },
    {
      name: 'a resolve release list',
      useCase: () => useGraphResolve({ q: ['SYN-TF1'], releases: [v2, v1] }),
      search: `?q=SYN-TF1&releases=${v1},${v2}`,
    },
    {
      name: 'a relationship browser',
      useCase: () => useGraphRelationship(mixed, { browser: 'synthetic-regulation' }),
      search: '?browser=synthetic-regulation',
    },
    {
      name: 'a relationship outcome',
      useCase: () => useGraphRelationship(mixed, { outcome: 'all' }),
      search: '?outcome=all',
    },
    {
      name: 'a relationship score, repeated and sorted',
      useCase: () => useGraphRelationship(mixed, { score: ['b::3', 'a:1:2'] }),
      search: '?score=a:1:2&score=b::3',
    },
    {
      name: 'a browser beside a release list, where the browser wins',
      useCase: () => useGraphEntity(tf1, { browser: 'synthetic-regulation', releases: [v1] } as never),
      search: '?browser=synthetic-regulation',
    },
    {
      name: 'an evidence browser',
      useCase: () => useGraphEvidence(mixed, { browser: 'synthetic-regulation' }),
      search: '?browser=synthetic-regulation',
    },
    {
      name: 'an evidence release list',
      useCase: () => useGraphEvidence(mixed, { releases: [v2, v1] }),
      search: `?releases=${v1},${v2}`,
    },
  ])('$name', async ({ useCase, search }) => {
    server.use(
      http.get(ENTITY, () => HttpResponse.json(recordedBody('entity-tf1'))),
      http.get(BROWSERS, () => HttpResponse.json(recordedBody('browsers'))),
      http.get(RESOLVE, () => HttpResponse.json(recordedBody('resolve-tf1-shared'))),
      http.get(EVIDENCE, () => HttpResponse.json(recordedBody('evidence-mixed'))),
      http.get(RELATIONSHIP, () => HttpResponse.json(recordedBody('relationship-mixed'))),
    )
    const { result } = renderHook(() => useCase(), { wrapper: graphWrapper() })
    await waitFor(() => expect(result.current.isSuccess).toBe(true), GRAPH_WAIT)
    expect(log.urls[0].search).toBe(search)
  })

  it('a slug holding a slash stays one path segment', async () => {
    server.use(http.get(`${BROWSERS}/:slug`, () => HttpResponse.json(recordedBody('browser-typed'))))
    const { result } = renderHook(() => useGraphBrowser('a/b'), { wrapper: graphWrapper() })
    await waitFor(() => expect(result.current.isSuccess).toBe(true), GRAPH_WAIT)
    expect(log.urls[0].pathname).toBe('/api/graph/v1/browsers/a%2Fb')
  })
})

describe('a bad answer', () => {
  it.each([
    { name: 'useGraphNetwork', path: NETWORK, useCase: () => useGraphNetwork(seeded) },
    { name: 'useGraphEvidence', path: EVIDENCE, useCase: () => useGraphEvidence(mixed) },
  ])('$name rejects a 2xx with no JSON object', async ({ path, useCase }) => {
    const counter = counting(path, () => HttpResponse.html('<html>'))
    const { result } = renderHook(() => useCase(), { wrapper: graphWrapper() })
    await waitFor(() => expect(result.current.isError).toBe(true), GRAPH_WAIT)
    expect(classifyGraphError(result.current.error).kind).toBe('bad_body')
    expect(counter.calls).toBe(1)
  })

  it.each<[string, () => Response, number]>([
    ['a JSON array', () => HttpResponse.json([]), 200],
    ['JSON null', () => HttpResponse.json(null), 200],
    ['a 201 with a JSON object', () => HttpResponse.json({ page: { next: null } }, { status: 201 }), 201],
  ])('%s is a bad body', async (_name, reply, status) => {
    const counter = counting(NETWORK, reply)
    const { result } = renderHook(() => useGraphNetwork(seeded), { wrapper: graphWrapper() })
    await waitFor(() => expect(result.current.isError).toBe(true), GRAPH_WAIT)
    expect(classifyGraphError(result.current.error)).toEqual({ kind: 'bad_body', status })
    expect(counter.calls).toBe(1)
  })

  it.each([
    ['an HTML 400', () => HttpResponse.html('<html>', { status: 400 }), 'server_refused'],
    ['an HTML 414', () => HttpResponse.html('<html>', { status: 414 }), 'server_refused'],
    ['a dropped connection', () => HttpResponse.error(), 'network'],
  ])('%s rejects as itself', async (_name, reply, kind) => {
    server.use(http.get(NETWORK, reply))
    const { result } = renderHook(() => useGraphNetwork(seeded, { retry: false }), { wrapper: graphWrapper() })
    await waitFor(() => expect(result.current.isError).toBe(true), GRAPH_WAIT)
    expect(classifyGraphError(result.current.error).kind).toBe(kind)
  })

  it('a dead token ends anonymous, under the visitor key', async () => {
    localStorage.setItem('openpip_access_token', plainToken)
    let calls = 0
    server.use(
      http.get(NETWORK, ({ request }) => {
        calls += 1
        return request.headers.get('Authorization') ? new HttpResponse(null, { status: 401 }) : jsonNetwork()
      }),
    )
    const client = newGraphClient()
    const { result } = renderHook(() => useGraphNetwork(seeded), { wrapper: graphWrapper(client) })
    await waitFor(() => expect(result.current.isSuccess).toBe(true), GRAPH_WAIT)
    expect(client.getQueryCache().findAll({ queryKey: ['graph', 'network', 'visitor'], type: 'active' })).toHaveLength(1)
    expect(calls).toBe(2)
  })
})

describe('the gallery', () => {
  it('reads one page, whatever page.next says', async () => {
    const body = recorded<GraphBrowsersResponse>('browsers').body
    const counter = counting(BROWSERS, () => HttpResponse.json({ ...body, page: { ...body.page, next: 'opaque' } }))
    const { result } = renderHook(() => useGraphBrowsers(), { wrapper: graphWrapper() })
    await waitFor(() => expect(result.current.isSuccess).toBe(true), GRAPH_WAIT)
    await pause(30)
    expect(counter.calls).toBe(1)
    expect('fetchNextPage' in result.current).toBe(false)
  })
})
