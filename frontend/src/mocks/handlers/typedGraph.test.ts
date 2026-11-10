import { describe, expect, it } from 'vitest'
import { apiClient } from '../../api/client'
import type { GraphParameterError } from '../../features/graph/types'
import { recorded } from '../../test/graphHooks'
import { recordedEntries } from '../fixtures/typedGraph/index'
import { demoRefs } from '../fixtures/typedGraph/recorded'
import { server } from '../server'
import source from './typedGraph.ts?raw'
import { typedGraphOffHandlers, typedGraphRoutes, typedGraphVisitorHandlers } from './typedGraph'

const { tf1, v1, v2, mixed } = demoRefs

const anyStatus = { validateStatus: () => true }
const resolveAllowed = ['q', 'namespace', 'taxon', 'entity_type', 'releases', 'browser']
const resolveSentence = `Allowed: ${resolveAllowed.join(', ')}.`

function get(url: string) {
  return apiClient.get(url, anyStatus)
}

describe('the default handlers', () => {
  it('serve a recording', async () => {
    const response = await get(`/graph/v1/network?seeds=${tf1}`)
    expect(response.status).toBe(200)
    expect(response.data).toEqual(recorded('network-tf1').body)
  })

  it.each([tf1, tf1.replace(':', '%3A')])('serve an entity spelled %s', async (spelling) => {
    const response = await get(`/graph/v1/entities/${spelling}`)
    expect(response.status).toBe(200)
    expect(response.data).toEqual(recorded('entity-tf1').body)
  })

  it('read a reordered list as one request', async () => {
    const response = await get(`/graph/v1/network?seeds=${tf1}&releases=${v2},${v1}`)
    expect(response.status).toBe(200)
    expect(response.data).toEqual(recorded('network-tf1-both-releases').body)
  })

  it('keep the order of resolve terms', async () => {
    const response = await get('/graph/v1/resolve?q=SYN-SHARED,SYN-TF1')
    expect(response.status).toBe(501)
  })
})

describe('a name a route does not take', () => {
  it('is the exact 400 the API sends', async () => {
    const response = await get('/graph/v1/resolve?q=x&foo=1')
    expect(response.status).toBe(400)
    expect(response.data).toEqual({
      detail: `Unknown parameter 'foo'. ${resolveSentence}`,
      errors: [{ parameter: 'foo', value: null, allowed: resolveAllowed, limit: null }],
    })
  })

  it("matches the recording's first problem and sentence", async () => {
    const response = await get('/graph/v1/resolve?q=x&foo=1')
    const body = recorded<GraphParameterError>('resolve-unknown-parameter').body
    expect(response.data.errors[0]).toEqual(body.errors[0])
    expect(body.detail.startsWith(`${response.data.detail} `)).toBe(true)
  })

  it('refuses the bracketed list axios sends by default', async () => {
    const response = await apiClient.get('/graph/v1/network', {
      params: { seeds: tf1, releases: [v1] },
      ...anyStatus,
    })
    expect(response.status).toBe(400)
    expect(response.data.errors[0].parameter).toBe('releases[]')
  })

  it('on a route with no parameters says so', async () => {
    const response = await get('/graph/v1/status?foo=1')
    expect(response.status).toBe(400)
    expect(response.data.detail).toBe("Unknown parameter 'foo'. This route takes no parameters.")
    expect(response.data.errors).toEqual([{ parameter: 'foo', value: null, allowed: [], limit: null }])
  })

  it('names each one, in one detail', async () => {
    const response = await get('/graph/v1/resolve?q=x&foo=1&bar=2')
    expect(response.status).toBe(400)
    expect(response.data.errors.map((problem: { parameter: string }) => problem.parameter)).toEqual(['foo', 'bar'])
    expect(response.data.detail).toBe(`Unknown parameter 'foo'. ${resolveSentence} Unknown parameter 'bar'. ${resolveSentence}`)
  })

  it("does not hide the page's recorded 400", async () => {
    const response = await get('/graph/v1/resolve?q=x&foo=1&entity_type=chemical')
    expect(response.status).toBe(400)
    expect(response.data).toEqual(recorded('resolve-unknown-parameter').body)
  })
})

describe('a miss', () => {
  it('is a 501 that names its key', async () => {
    const response = await get('/graph/v1/network?seeds=entity:999')
    expect(response.status).toBe(501)
    expect(response.data.key).toBe('/api/graph/v1/network?seeds=entity:999')
  })

  it.each(['seeds', 'layers', 'outcome', 'evidence_basis', 'effect', 'method', 'level', 'releases'])(
    'reads %s as one sorted, deduped list',
    async (name) => {
      const response = await get(`/graph/v1/network?${name}=b,a,b`)
      expect(response.status).toBe(501)
      expect(response.data.key).toBe(`/api/graph/v1/network?${name}=a,b`)
    },
  )

  it('is a 501 for a write', async () => {
    const response = await apiClient.post('/graph/v1/status', {}, anyStatus)
    expect(response.status).toBe(501)
  })

  it('is a 501 for a path no route serves', async () => {
    const response = await get('/graph/v1/nope')
    expect(response.status).toBe(501)
  })

  it('is a 501 for format, which nothing sends', async () => {
    const response = await get('/graph/v1/status?format=json')
    expect(response.status).toBe(501)
  })
})

describe('the visitor handlers', () => {
  it("answer the visitor's recordings", async () => {
    server.use(...typedGraphVisitorHandlers)
    const network = await get(`/graph/v1/network?seeds=${tf1}`)
    expect(network.status).toBe(200)
    expect(network.data).toEqual(recorded('network-tf1-visitor').body)
    expect((await get(`/graph/v1/relationships/${mixed}`)).status).toBe(404)
  })

  it("fall back to staff's status, which varies by no caller", async () => {
    server.use(...typedGraphVisitorHandlers)
    const status = await get('/graph/v1/status')
    expect(status.status).toBe(200)
    expect(status.data).toEqual(recorded('status-on').body)
  })

  it("fall back to staff's vocabularies, which vary by no caller", async () => {
    server.use(...typedGraphVisitorHandlers)
    const vocabularies = await get('/graph/v1/vocabularies')
    expect(vocabularies.status).toBe(200)
    expect(vocabularies.data).toEqual(recorded('vocabularies').body)
  })

  it("do not fall back to staff's entities", async () => {
    server.use(...typedGraphVisitorHandlers)
    expect((await get(`/graph/v1/entities/${tf1}`)).status).toBe(501)
  })
})

describe('the switch-off handlers', () => {
  it('answer status as recorded', async () => {
    server.use(...typedGraphOffHandlers)
    const response = await get('/graph/v1/status')
    expect(response.status).toBe(200)
    expect(response.data).toEqual(recorded('status-off').body)
  })

  it.each(
    typedGraphRoutes
      .filter((route) => !route.path.endsWith('/status'))
      .map((route) => route.path.replace(/^\/api/, '').replace(/\{\w+\}/, 'x')),
  )('answer %s with the JSON 404', async (path) => {
    server.use(...typedGraphOffHandlers)
    const response = await get(path)
    expect(response.status).toBe(404)
    expect(response.data).toEqual({ detail: 'Not found.' })
  })

  it('still refuse a name status does not take', async () => {
    server.use(...typedGraphOffHandlers)
    expect((await get('/graph/v1/status?foo=1')).status).toBe(400)
  })
})

describe('every recording', () => {
  it.each(recordedEntries.map((entry) => [entry.name, entry] as const))('%s is served', async (_name, entry) => {
    server.resetHandlers()
    if (entry.caller === 'visitor') server.use(...typedGraphVisitorHandlers)
    if (entry.caller === 'off') server.use(...typedGraphOffHandlers)
    const query = entry.query.map(([name, value]) => `${name}=${value}`).join('&')
    const response = await get(`${entry.path.replace(/^\/api/, '')}${query ? `?${query}` : ''}`)
    expect(response.status).toBe(entry.status)
    expect(response.data).toEqual(entry.body)
  })
})

describe('the module', () => {
  it('imports no fixture statically', () => {
    const imports = (source as string).split('\n').filter((line) => /^import\b/.test(line))
    expect(imports.length).toBeGreaterThan(0)
    for (const line of imports) {
      expect(line === "import { http, HttpResponse } from 'msw'" || line.startsWith('import type '), line).toBe(true)
    }
    expect(source).toContain("import('../fixtures/typedGraph/index')")
  })
})
