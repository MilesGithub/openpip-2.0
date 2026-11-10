import { act, renderHook, waitFor } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import type { JsonBodyType } from 'msw'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { evidencePageNoRowsWithNext } from '../../mocks/fixtures/typedGraph/extras'
import { demoRefs } from '../../mocks/fixtures/typedGraph/recorded'
import { server } from '../../mocks/server'
import { GRAPH_WAIT, graphWrapper, recorded, recordRequests } from '../../test/graphHooks'
import type { RequestLog } from '../../test/graphHooks'
import { classifyGraphError } from './errors'
import { useGraphEvidence } from './relationships'

const EVIDENCE = '/api/graph/v1/relationships/:ref/evidence'

let log: RequestLog

beforeEach(() => {
  log = recordRequests()
})

afterEach(() => {
  log.stop()
  expect(log.unhandled).toEqual([])
})

describe('useGraphEvidence', () => {
  it('follows page.next to null', async () => {
    const { result } = renderHook(() => useGraphEvidence(demoRefs.mixed, { page_size: 1 }), {
      wrapper: graphWrapper(),
    })
    await waitFor(() => expect(result.current.isSuccess).toBe(true), GRAPH_WAIT)
    expect(result.current.data?.pages[0]).toEqual(recorded('evidence-mixed-page-1').body)
    expect(log.urls[0].search).toBe('?page_size=1')
    expect(log.urls[0].pathname).toBe(`/api/graph/v1/relationships/${demoRefs.mixed.replace(':', '%3A')}/evidence`)
    expect(result.current.hasNextPage).toBe(true)

    await act(async () => {
      await result.current.fetchNextPage()
    })
    await waitFor(() => expect(result.current.data?.pages).toHaveLength(2), GRAPH_WAIT)
    expect(result.current.data?.pages[1]).toEqual(recorded('evidence-mixed-page-2').body)
    expect(log.urls[1].search).toBe('?after=evidence:21&page_size=1')
    expect(result.current.hasNextPage).toBe(false)
  })

  it('goes on past a page with no rows and a next', async () => {
    server.use(
      http.get(EVIDENCE, ({ request }) =>
        HttpResponse.json(
          new URL(request.url).searchParams.get('after') === 'evidence:22'
            ? recorded<JsonBodyType>('evidence-mixed').body
            : evidencePageNoRowsWithNext,
        ),
      ),
    )
    const { result } = renderHook(() => useGraphEvidence(demoRefs.mixed), { wrapper: graphWrapper() })
    await waitFor(() => expect(result.current.isSuccess).toBe(true), GRAPH_WAIT)
    expect(result.current.data?.pages[0].evidence).toEqual([])
    expect(result.current.hasNextPage).toBe(true)

    await act(async () => {
      await result.current.fetchNextPage()
    })
    await waitFor(() => expect(result.current.data?.pages).toHaveLength(2), GRAPH_WAIT)
    expect(log.urls[1].search).toBe('?after=evidence:22')
    expect(result.current.data?.pages[1]).toEqual(recorded('evidence-mixed').body)
  })

  it('ends at a page with a row and no next', async () => {
    const { result } = renderHook(() => useGraphEvidence(demoRefs.prediction), { wrapper: graphWrapper() })
    await waitFor(() => expect(result.current.isSuccess).toBe(true), GRAPH_WAIT)
    expect(result.current.data?.pages[0].evidence).toHaveLength(1)
    expect(result.current.hasNextPage).toBe(false)
    await act(async () => {
      await result.current.fetchNextPage()
    })
    expect(log.urls).toHaveLength(1)
  })

  it('keeps earlier pages when a next page fails', async () => {
    const { result } = renderHook(() => useGraphEvidence(demoRefs.mixed, { page_size: 1 }, { retry: false }), {
      wrapper: graphWrapper(),
    })
    await waitFor(() => expect(result.current.isSuccess).toBe(true), GRAPH_WAIT)
    server.use(http.get(EVIDENCE, () => HttpResponse.html('<html>', { status: 500 })))
    await act(async () => {
      await result.current.fetchNextPage()
    })
    await waitFor(() => expect(result.current.isFetchNextPageError).toBe(true), GRAPH_WAIT)
    expect(result.current.data?.pages).toHaveLength(1)
  })

  it.each<[string, JsonBodyType]>([
    ['an empty object', {}],
    ['a null page', { page: null }],
    ['a list for a page', { page: [] }],
    ['another shape', { detail: 'maintenance' }],
  ])('refuses %s as a bad body, and does not hang', async (_name, body) => {
    let calls = 0
    server.use(
      http.get(EVIDENCE, () => {
        calls += 1
        return HttpResponse.json(body)
      }),
    )
    const { result } = renderHook(() => useGraphEvidence(demoRefs.mixed), { wrapper: graphWrapper() })
    await waitFor(() => expect(result.current.isError).toBe(true), GRAPH_WAIT)
    expect(classifyGraphError(result.current.error)).toEqual({ kind: 'bad_body', status: 200 })
    expect(calls).toBe(1)
  })
})
