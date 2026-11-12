import { act, cleanup, render, renderHook, waitFor } from '@testing-library/react'
import type { Core } from 'cytoscape'
import { http, HttpResponse } from 'msw'
import { useState } from 'react'
import { afterEach, beforeEach, describe, expect, it, onTestFinished, vi } from 'vitest'
import { classifyGraphError } from '../../../api/graph/errors'
import type { GraphNetworkParams } from '../../../api/graph/params'
import { server } from '../../../mocks/server'
import { GRAPH_WAIT, graphWrapper, recorded, recordRequests } from '../../../test/graphHooks'
import type { RequestLog } from '../../../test/graphHooks'
import type { GraphNetworkResponse } from '../types'
import type { TypedSelectionEvent } from './graph'
import { networkView } from './networkView'
import type { NetworkView, NetworkViewInput } from './networkView'
import { TypedNetwork } from './TypedNetwork'
import { useTypedNetwork } from './useTypedNetwork'
import type { TypedNetworkResult } from './useTypedNetwork'

const log = vi.hoisted(() => ({ instances: [], runs: new Map() }))
vi.mock('react-cytoscapejs', async (importOriginal) =>
  (await import('../../../test/headlessCytoscape')).headlessCytoscapeModule(importOriginal, log))

// Cytoscape and React report a bad value or an unwrapped update through the console instead of throwing.
const REPORTED = /cytoscape|is invalid|not a valid|act\(/i

beforeEach(() => {
  log.instances.length = 0
  log.runs.clear()
  vi.spyOn(console, 'warn')
  vi.spyOn(console, 'error')
})

afterEach(() => {
  // Unmounted here rather than after this hook, so that what the unmount reports is checked too.
  cleanup()
  vi.useRealTimers()
  const calls = [...vi.mocked(console.warn).mock.calls, ...vi.mocked(console.error).mock.calls]
  vi.restoreAllMocks()
  expect(calls.map((args) => args.map(String).join(' ')).filter((text) => REPORTED.test(text))).toEqual([])
})

const NETWORK = '/api/graph/v1/network'
const TF1: GraphNetworkParams = { seeds: ['entity:7'] }

function body(name: string): GraphNetworkResponse {
  return recorded<GraphNetworkResponse>(name).body
}

/** The network requests sent, each as its decoded query string. */
function sent(requests: RequestLog): string[] {
  return requests.urls
    .filter((url) => url.pathname === NETWORK)
    .map((url) => Array.from(url.searchParams.entries()).map(([name, value]) => `${name}=${value}`).join('&'))
}

function requestLog(): RequestLog {
  const requests = recordRequests()
  onTestFinished(() => requests.stop())
  return requests
}

/** As status.test.ts does: the timers are advanced inside act, so the debounce's state update is flushed there. */
async function advance(ms: number): Promise<void> {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms)
  })
}

function renderTyped(params: GraphNetworkParams | null) {
  return renderHook(({ current }: { current: GraphNetworkParams | null }) => useTypedNetwork(current), {
    initialProps: { current: params },
    wrapper: graphWrapper(),
  })
}

function json503(): Response {
  return HttpResponse.json({ detail: 'Too expensive.' }, { status: 503 })
}

describe('networkView, the first rule that applies', () => {
  const tf1 = body('network-tf1')
  const v1 = body('network-tf1-v1')
  const failure = new Error('refused')
  const input = (
    result: Partial<NetworkViewInput['result']>,
    flags: Partial<Pick<NetworkViewInput, 'disabled' | 'debouncing'>> = {},
  ): NetworkViewInput => ({
    disabled: false,
    debouncing: false,
    ...flags,
    result: { data: undefined, status: 'pending', fetchStatus: 'idle', isPlaceholderData: false, error: null, ...result },
  })
  const rows: [string, NetworkViewInput, Pick<NetworkView, 'state' | 'response'>][] = [
    ['1: a disabled key whose body React Query keeps, a success at rest',
      input({ data: tf1, status: 'success' }, { disabled: true }), { state: 'disabled', response: null }],
    ['1: disabled before an error', input({ status: 'error', error: failure }, { disabled: true }), { state: 'disabled', response: null }],
    ['2: an errored key fetched again, with its body',
      input({ data: tf1, status: 'error', fetchStatus: 'fetching', error: failure }), { state: 'updating', response: tf1 }],
    ['2: an errored key fetched again, with no body',
      input({ status: 'error', fetchStatus: 'fetching', error: failure }), { state: 'loading', response: null }],
    ['3: a failed refetch keeps its own body', input({ data: tf1, status: 'error', error: failure }), { state: 'error', response: tf1 }],
    ['3: a JSON 503 under a new key has no body', input({ status: 'error', error: failure }), { state: 'error', response: null }],
    ['3: placeholder data answers another key',
      input({ data: v1, status: 'error', isPlaceholderData: true, error: failure }), { state: 'error', response: null }],
    ['4: nothing yet', input({ status: 'pending', fetchStatus: 'fetching' }), { state: 'loading', response: null }],
    ['4: nothing yet while debouncing', input({}, { debouncing: true }), { state: 'loading', response: null }],
    ['5: debouncing', input({ data: tf1, status: 'success' }, { debouncing: true }), { state: 'updating', response: tf1 }],
    ['5: placeholder data',
      input({ data: v1, status: 'success', fetchStatus: 'fetching', isPlaceholderData: true }), { state: 'updating', response: v1 }],
    ['5: a refetch in the background', input({ data: tf1, status: 'success', fetchStatus: 'fetching' }), { state: 'updating', response: tf1 }],
    ['6: ready', input({ data: tf1, status: 'success' }), { state: 'ready', response: tf1 }],
  ]

  it.each(rows)('%s', (_name, given, expected) => {
    const view = networkView(given)
    expect(view.state).toBe(expected.state)
    expect(view.response).toBe(expected.response)
    if (view.state === 'error') expect(view.error).toBe(failure)
  })
})

describe('useTypedNetwork over MSW', () => {
  it('requests the first params at once, and rapid changes inside the debounce once, for the last params', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const requests = requestLog()
    const { result, rerender } = renderTyped(TF1)
    // Fetching from the first render: the settled key starts as the key.
    expect(result.current.state).toBe('loading')
    expect(result.current.result.fetchStatus).toBe('fetching')
    await waitFor(() => expect(result.current.state).toBe('ready'), GRAPH_WAIT)
    expect(sent(requests)).toEqual(['seeds=entity:7'])

    rerender({ current: { ...TF1, outcome: 'all' } })
    expect(result.current.state).toBe('updating')
    await advance(50)
    rerender({ current: { seeds: ['entity:7'], releases: ['release:1'] } })
    await advance(50)
    rerender({ current: { seeds: ['entity:7'], releases: ['release:1', 'release:2'] } })
    await advance(50)
    expect(result.current.state).toBe('updating')
    expect(result.current.graph?.query?.releases).toBeNull()
    expect(sent(requests)).toEqual(['seeds=entity:7'])

    await advance(350)
    await waitFor(() => expect(result.current.state).toBe('ready'), GRAPH_WAIT)
    expect(sent(requests)).toEqual(['seeds=entity:7', 'seeds=entity:7&releases=release:1,release:2'])
    expect(result.current.graph?.releases.map((release) => release.id)).toEqual(['release:1', 'release:2'])
  })

  it('reads loading, never disabled, through the debounce from no params to a seed', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const requests = requestLog()
    const states: TypedNetworkResult['state'][] = []
    const { result, rerender } = renderHook(
      ({ current }: { current: GraphNetworkParams | null }) => {
        const typed = useTypedNetwork(current)
        states.push(typed.state)
        return typed
      },
      { initialProps: { current: null as GraphNetworkParams | null }, wrapper: graphWrapper() },
    )
    expect(result.current.state).toBe('disabled')
    states.length = 0

    rerender({ current: TF1 })
    expect(result.current.state).toBe('loading')
    await advance(100)
    expect(result.current.state).toBe('loading')
    expect(sent(requests)).toEqual([])
    await advance(50)
    expect(result.current.state).toBe('loading')

    await advance(200)
    await waitFor(() => expect(result.current.state).toBe('ready'), GRAPH_WAIT)
    expect(states).not.toContain('disabled')
    expect(Array.from(new Set(states))).toEqual(['loading', 'ready'])
    expect(sent(requests)).toEqual(['seeds=entity:7'])
  })

  it('follows the params in the same render with debounceMs 0', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const requests = requestLog()
    const { result, rerender } = renderHook(
      ({ current }: { current: GraphNetworkParams | null }) => useTypedNetwork(current, { debounceMs: 0 }),
      { initialProps: { current: null as GraphNetworkParams | null }, wrapper: graphWrapper() },
    )
    rerender({ current: TF1 })
    // No timer stands between the params and the query.
    expect(result.current.result.fetchStatus).toBe('fetching')
    await waitFor(() => expect(result.current.state).toBe('ready'), GRAPH_WAIT)

    rerender({ current: { ...TF1, outcome: 'all' } })
    expect(result.current.result.isPlaceholderData).toBe(true)
    await waitFor(() => expect(result.current.graph?.query?.outcome).toEqual(['all']), GRAPH_WAIT)

    rerender({ current: TF1 })
    // A key still in the cache reads ready at once, with its own graph.
    expect(result.current.state).toBe('ready')
    expect(result.current.graph?.query?.outcome).toBeNull()
    expect(sent(requests)).toEqual(['seeds=entity:7', 'seeds=entity:7&outcome=all'])
  })

  it('gives disabled and no graph for a key disabled with [], although React Query keeps the body', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const requests = requestLog()
    const { result, rerender } = renderTyped(TF1)
    await waitFor(() => expect(result.current.state).toBe('ready'), GRAPH_WAIT)

    rerender({ current: { ...TF1, effect: [] } })
    // From the current params, at once.
    expect(result.current.state).toBe('disabled')
    expect(result.current.graph).toBeNull()
    await advance(350)
    expect(result.current.state).toBe('disabled')
    expect(result.current.graph).toBeNull()
    expect(result.current.result.data).toEqual(body('network-tf1'))
    expect(sent(requests)).toEqual(['seeds=entity:7'])
  })

  it('gives an error and no graph for a JSON 503 under a new key, and the first body on returning to its key', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    server.use(
      http.get(NETWORK, ({ request }) => (new URL(request.url).searchParams.get('seeds') === 'entity:8' ? json503() : undefined)),
    )
    const requests = requestLog()
    const { result, rerender } = renderTyped(TF1)
    await waitFor(() => expect(result.current.state).toBe('ready'), GRAPH_WAIT)
    const first = result.current.graph

    rerender({ current: { seeds: ['entity:8'] } })
    await advance(350)
    await waitFor(() => expect(result.current.state).toBe('error'), GRAPH_WAIT)
    expect(result.current.graph).toBeNull()
    expect(classifyGraphError(result.current.error).kind).toBe('too_expensive')

    rerender({ current: TF1 })
    await advance(350)
    await waitFor(() => expect(result.current.state).toBe('ready'), GRAPH_WAIT)
    expect(result.current.graph?.edges).toEqual(body('network-tf1').edges)
    expect(result.current.graph?.query).toEqual(first?.query)
    expect(sent(requests)).toEqual(['seeds=entity:7', 'seeds=entity:8'])
  })

  it('sends nothing with enabled: false', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const requests = requestLog()
    const { result } = renderHook(() => useTypedNetwork(TF1, { enabled: false }), { wrapper: graphWrapper() })
    expect(result.current.state).toBe('disabled')
    await advance(400)
    expect(result.current.state).toBe('disabled')
    expect(result.current.graph).toBeNull()
    expect(sent(requests)).toEqual([])
  })

  it('keeps one graph object across re-renders of one response', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const requests = requestLog()
    const { result, rerender } = renderTyped(TF1)
    await waitFor(() => expect(result.current.state).toBe('ready'), GRAPH_WAIT)
    const graph = result.current.graph
    expect(graph).not.toBeNull()
    rerender({ current: { seeds: ['entity:7'] } })
    rerender({ current: { seeds: ['entity:7'] } })
    await advance(350)
    expect(result.current.state).toBe('ready')
    expect(result.current.graph).toBe(graph)
    expect(sent(requests)).toEqual(['seeds=entity:7'])
  })

  it("after an error, shows the last body drawn while a new key is pending, and its graph names that body's request", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    let release = () => {}
    const gate = new Promise<void>((resolve) => {
      release = resolve
    })
    server.use(
      http.get(NETWORK, async ({ request }) => {
        const search = new URL(request.url).searchParams
        if (search.get('seeds') === 'entity:8') return json503()
        if (search.get('outcome') !== 'all') return undefined
        await gate
        return HttpResponse.json(body('network-tf1-outcome-all'))
      }),
    )
    const requests = requestLog()
    const { result, rerender } = renderTyped(TF1)
    await waitFor(() => expect(result.current.state).toBe('ready'), GRAPH_WAIT)
    const a = result.current.graph

    rerender({ current: { seeds: ['entity:8'] } })
    await advance(350)
    await waitFor(() => expect(result.current.state).toBe('error'), GRAPH_WAIT)
    expect(result.current.graph).toBeNull()

    rerender({ current: { ...TF1, outcome: 'all' } })
    // Until the debounce ends, the state answers the settled params, whose answer was the error.
    expect([result.current.state, result.current.graph]).toEqual(['error', null])
    await advance(100)
    expect([result.current.state, result.current.graph]).toEqual(['error', null])
    await advance(250)
    await waitFor(() => expect(sent(requests)).toHaveLength(3), GRAPH_WAIT)
    expect(result.current.state).toBe('updating')
    expect(result.current.result.isPlaceholderData).toBe(true)
    expect(result.current.graph?.edges).toEqual(a?.edges)
    expect(result.current.graph?.query).toEqual(a?.query)
    expect(result.current.graph?.query?.outcome).toBeNull()

    release()
    await waitFor(() => expect(result.current.state).toBe('ready'), GRAPH_WAIT)
    expect(result.current.graph?.query?.outcome).toEqual(['all'])
    expect(sent(requests)).toEqual(['seeds=entity:7', 'seeds=entity:8', 'seeds=entity:7&outcome=all'])
  })
})

describe('the hook and the canvas together', () => {
  it('send no request for a tap or for an equal new params object', async () => {
    const requests = requestLog()
    const onSelected = vi.fn<(selection: TypedSelectionEvent | null) => void>()
    function Harness({ params }: { params: GraphNetworkParams }) {
      const { graph, state, error } = useTypedNetwork(params)
      const [selected, setSelected] = useState<TypedSelectionEvent | null>(null)
      const select = (selection: TypedSelectionEvent | null) => {
        onSelected(selection)
        setSelected(selection)
      }
      return <TypedNetwork graph={graph} state={state} error={error} selected={selected} onSelect={select} />
    }
    const view = render(<Harness params={{ seeds: ['entity:7'] }} />, { wrapper: graphWrapper() })
    await waitFor(() => expect(log.instances).toHaveLength(1), GRAPH_WAIT)
    const cy = log.instances[0] as Core
    expect(sent(requests)).toEqual(['seeds=entity:7'])

    act(() => {
      cy.$id('relationship:5').emit('tap')
    })
    expect(onSelected).toHaveBeenCalledTimes(1)
    expect(cy.$id('relationship:5').hasClass('is-selected')).toBe(true)
    view.rerender(<Harness params={{ seeds: ['entity:7'] }} />)
    await act(async () => {
      await new Promise<void>((resolve) => setTimeout(resolve, 500))
    })
    expect(sent(requests)).toEqual(['seeds=entity:7'])
    expect((log.runs as Map<Core, number>).get(cy)).toBe(1)
  })
})
