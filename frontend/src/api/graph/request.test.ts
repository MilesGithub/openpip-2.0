import { afterEach, describe, expect, it, vi } from 'vitest'
import type { GraphStatus } from '../../features/graph/types'
import { demoRefs } from '../../mocks/fixtures/typedGraph/recorded'
import { recorded } from '../../test/graphHooks'
import { apiClient } from '../client'
import { GraphRequestTooLongError } from './errors'
import { GRAPH_MAX_REQUEST_LINE, graphData, graphGet, graphRequestLineBytes } from './request'
import { chunkResolveTerms, GRAPH_RESOLVE_MAX_TERMS } from './resolve'

const RESOLVE = '/graph/v1/resolve'
const OVERHEAD = 'GET /api/graph/v1/resolve?q= HTTP/1.1'.length

afterEach(() => {
  vi.restoreAllMocks()
})

function spyOnGet() {
  return vi.spyOn(apiClient, 'get').mockResolvedValue({ data: {}, status: 200 })
}

describe('graphRequestLineBytes', () => {
  it('counts the whole request line', () => {
    expect(graphRequestLineBytes(RESOLVE, { q: 'abc' })).toBe('GET /api/graph/v1/resolve?q=abc HTTP/1.1'.length)
  })

  it('counts an apostrophe as the browser sends it', () => {
    expect(graphRequestLineBytes(RESOLVE, { q: "it's" })).toBe('GET /api/graph/v1/resolve?q=it%27s HTTP/1.1'.length)
  })

  it('counts percent-encoded bytes, not characters', () => {
    expect(graphRequestLineBytes(RESOLVE, { q: 'αβ' })).toBe('GET /api/graph/v1/resolve?q=%CE%B1%CE%B2 HTTP/1.1'.length)
  })
})

describe('the request-line limit', () => {
  it('sends a line of exactly 4,094 bytes', async () => {
    const get = spyOnGet()
    const q = 'a'.repeat(4094 - OVERHEAD)
    expect(graphRequestLineBytes(RESOLVE, { q })).toBe(4094)
    await graphGet(RESOLVE, { q })
    expect(get).toHaveBeenCalledTimes(1)
  })

  it('refuses a line of 4,095 bytes and sends nothing', async () => {
    const get = spyOnGet()
    const error = await graphGet(RESOLVE, { q: 'a'.repeat(4095 - OVERHEAD) }).catch((caught: unknown) => caught)
    expect(error).toBeInstanceOf(GraphRequestTooLongError)
    expect(error).toMatchObject({ bytes: 4095, limit: 4094 })
    expect(get).not.toHaveBeenCalled()
  })
})

describe('the serializer', () => {
  it('is set per request, so no legacy call can change', async () => {
    const get = spyOnGet()
    await graphData(RESOLVE, { q: 'a' })
    expect(apiClient.defaults.paramsSerializer).toBeUndefined()
    expect(get.mock.calls[0][1]?.paramsSerializer).toEqual({ indexes: null })
  })
})

describe('chunkResolveTerms', () => {
  const terms = (count: number, length: number) =>
    Array.from({ length: count }, (_, index) => String(index).padStart(length, 'x'))
  const sizes = (chunks: string[][]) => chunks.map((chunk) => chunk.length)

  it('splits 30 short terms at the cap', () => {
    expect(sizes(chunkResolveTerms({ q: terms(30, 3) }))).toEqual([25, 5])
  })

  it('splits 21 terms of 200 ASCII at the line budget', () => {
    expect(sizes(chunkResolveTerms({ q: terms(21, 200) }))).toEqual([20, 1])
  })

  it('keeps 25 terms of 161 together and splits 25 of 162', () => {
    expect(sizes(chunkResolveTerms({ q: terms(25, 161) }))).toEqual([25])
    expect(sizes(chunkResolveTerms({ q: terms(25, 162) }))).toEqual([24, 1])
  })

  it('counts the other parameters in the line budget', () => {
    expect(sizes(chunkResolveTerms({ q: terms(25, 161), releases: [demoRefs.v1] }))).toEqual([24, 1])
  })

  it('keeps the order of the terms', () => {
    const q = terms(30, 3)
    expect(chunkResolveTerms({ q }).flat()).toEqual(q)
  })

  it('honours maxTerms', () => {
    expect(sizes(chunkResolveTerms({ q: terms(5, 3) }, 2))).toEqual([2, 2, 1])
  })

  it('leaves a term past the line budget alone in its chunk', () => {
    const long = 'z'.repeat(5000)
    expect(chunkResolveTerms({ q: ['a', long, 'b'] })).toEqual([['a'], [long], ['b']])
    expect(chunkResolveTerms({ q: [long, 'b'] })).toEqual([[long], ['b']])
  })

  it('leaves a term that cannot be encoded alone in its chunk', () => {
    expect(chunkResolveTerms({ q: ['a', '\uDC00', 'b'] })).toEqual([['a'], ['\uDC00'], ['b']])
  })

  it('gives no chunk over the cap or the budget', () => {
    const chunks = chunkResolveTerms({ q: terms(60, 170) })
    expect(chunks.length).toBeGreaterThan(1)
    for (const chunk of chunks) {
      expect(chunk.length).toBeLessThanOrEqual(GRAPH_RESOLVE_MAX_TERMS)
      expect(graphRequestLineBytes(RESOLVE, { q: chunk.join(',') })).toBeLessThanOrEqual(GRAPH_MAX_REQUEST_LINE)
    }
  })

  it('does not throw on a state the hook treats as disabled', () => {
    expect(chunkResolveTerms({ q: ['a', 'b'], releases: [] })).toEqual([['a', 'b']])
    expect(chunkResolveTerms({ q: [] })).toEqual([])
    expect(chunkResolveTerms({ q: [null] })).toEqual([])
  })

  it("has the API's cap", () => {
    expect(GRAPH_RESOLVE_MAX_TERMS).toBe(recorded<GraphStatus>('status-on').body.limits.resolve_max_terms)
  })
})
