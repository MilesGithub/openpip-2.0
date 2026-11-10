import { act, renderHook, waitFor } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { GraphFeatureName, GraphStatus } from '../../features/graph/types'
import { typedGraphOffHandlers } from '../../mocks/handlers/typedGraph'
import { server } from '../../mocks/server'
import { GRAPH_WAIT, graphWrapper, recorded } from '../../test/graphHooks'
import { graphFeatureState, useGraphFeature, useGraphStatus } from './status'

const STATUS = '/api/graph/v1/status'

const statusOn = () => recorded<GraphStatus>('status-on').body
const statusOff = () => recorded<GraphStatus>('status-off').body

function renderStatus() {
  return renderHook(() => useGraphStatus({ retry: false }), { wrapper: graphWrapper() })
}

afterEach(() => {
  vi.useRealTimers()
})

describe('a well-formed 200', () => {
  it('answers on for a true feature', async () => {
    const { result } = renderStatus()
    await waitFor(() => expect(result.current.isSuccess).toBe(true), GRAPH_WAIT)
    expect(graphFeatureState(result.current, 'network')).toBe('on')
  })

  it('answers off for a false feature', async () => {
    const body = statusOn()
    server.use(http.get(STATUS, () => HttpResponse.json({ ...body, features: { ...body.features, datasets: false } })))
    const { result } = renderStatus()
    await waitFor(() => expect(result.current.isSuccess).toBe(true), GRAPH_WAIT)
    expect(graphFeatureState(result.current, 'datasets')).toBe('off')
  })

  it('answers off for every served key under the switch-off handlers', async () => {
    server.use(...typedGraphOffHandlers)
    const { result } = renderStatus()
    await waitFor(() => expect(result.current.isSuccess).toBe(true), GRAPH_WAIT)
    expect(result.current.data).toEqual(statusOff())
    const names = Object.keys(result.current.data?.features ?? {}) as GraphFeatureName[]
    expect(names).toHaveLength(8)
    for (const name of names) {
      expect(graphFeatureState(result.current, name)).toBe('off')
    }
  })
})

describe('anything else is unknown, never off', () => {
  it.each([
    ['429', () => HttpResponse.json({ detail: 'x' }, { status: 429 })],
    ['an HTML 500', () => HttpResponse.html('<html>', { status: 500 })],
    ['a dropped connection', () => HttpResponse.error()],
    ['a 200 HTML page', () => HttpResponse.html('<html>')],
    ['a 204', () => new HttpResponse(null, { status: 204 })],
    ['a 200 of another shape', () => HttpResponse.json({ detail: 'x' })],
    ['a 200 whose features are a list', () => HttpResponse.json({ typed_graph: true, features: [] })],
    ['a 200 whose features are null', () => HttpResponse.json({ typed_graph: true, features: null })],
    ['a 200 whose features are a string', () => HttpResponse.json({ typed_graph: true, features: 'x' })],
    ['a 200 with no typed_graph', () => HttpResponse.json({ features: { network: true } })],
    ['a status-shaped 203', () => HttpResponse.json({ typed_graph: true, features: { network: true } }, { status: 203 })],
  ])('%s', async (_name, reply) => {
    server.use(http.get(STATUS, reply))
    const { result } = renderStatus()
    await waitFor(() => expect(result.current.isError).toBe(true), GRAPH_WAIT)
    expect(graphFeatureState(result.current, 'network')).toBe('unknown')
  })

  it('a feature missing from the body', () => {
    expect(graphFeatureState({ data: { features: {} } as GraphStatus, isLoading: false }, 'network')).toBe('unknown')
  })
})

describe('graphFeatureState', () => {
  it('reads loading on a first load', () => {
    expect(graphFeatureState({ isLoading: true }, 'network')).toBe('loading')
  })

  it("keeps the last 200's on through a failed refetch", async () => {
    const { result } = renderStatus()
    await waitFor(() => expect(result.current.isSuccess).toBe(true), GRAPH_WAIT)
    server.use(http.get(STATUS, () => HttpResponse.html('<html>', { status: 500 })))
    await act(() => result.current.refetch())
    await waitFor(() => expect(result.current.isRefetchError).toBe(true), GRAPH_WAIT)
    expect(graphFeatureState(result.current, 'network')).toBe('on')
  })

  it('says unknown in place of an off whose refetch failed', () => {
    const data = { features: { network: false } } as unknown as GraphStatus
    expect(graphFeatureState({ data, isLoading: false, isRefetchError: true }, 'network')).toBe('unknown')
    expect(graphFeatureState({ data, isLoading: false }, 'network')).toBe('off')
  })
})

describe('useGraphFeature', () => {
  it('re-reads status while a tab is left open', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    let calls = 0
    server.use(
      http.get(STATUS, () => {
        calls += 1
        return HttpResponse.json(statusOn())
      }),
    )
    const { result } = renderHook(() => useGraphFeature('network'), { wrapper: graphWrapper() })
    await waitFor(() => expect(result.current).toBe('on'), GRAPH_WAIT)
    server.use(
      http.get(STATUS, () => {
        calls += 1
        return HttpResponse.json(statusOff())
      }),
    )
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5 * 60 * 1000)
    })
    await waitFor(() => expect(result.current).toBe('off'), GRAPH_WAIT)
    expect(calls).toBe(2)
  })
})
