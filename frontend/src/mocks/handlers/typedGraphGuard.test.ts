import { describe, expect, it, vi } from 'vitest'
import { apiClient } from '../../api/client'
import { server } from '../server'
import { typedGraphOffHandlers } from './typedGraph'

// The lazy fixture import as it fails at a file's teardown (loadTables): both guards must answer it. Nothing
// here imports the fixtures statically, test/graphHooks.ts and test/graphRoutes.tsx among them, or the mock
// would fail the file before its row. The row takes none of the other files' hooks: its 501s are its subject.
vi.mock('../fixtures/typedGraph/index', () => {
  throw new Error('torn down')
})

describe('the handler guard (F4)', () => {
  it('HG-1: with the fixtures unloadable, answer() and the off catch-all each answer their 501, logging nothing', async () => {
    const unhandled: string[] = []
    const record = ({ request }: { request: Request }) => {
      unhandled.push(request.url)
    }
    server.events.on('request:unhandled', record)
    const warn = vi.spyOn(console, 'warn')
    const error = vi.spyOn(console, 'error')
    try {
      const status = await apiClient.get('/graph/v1/status', { validateStatus: () => true })
      expect(status.status).toBe(501)
      expect(status.data.detail).toBe('No recorded typed-graph response for GET /api/graph/v1/status.')
      server.use(...typedGraphOffHandlers)
      const network = await apiClient.get('/graph/v1/network?seeds=entity:7', { validateStatus: () => true })
      expect(network.status).toBe(501)
      expect(network.data.detail).toBe('No recorded typed-graph response for GET /api/graph/v1/network.')
      expect([...warn.mock.calls, ...error.mock.calls]).toEqual([])
      expect(unhandled).toEqual([])
    } finally {
      server.events.removeListener('request:unhandled', record)
      vi.restoreAllMocks()
    }
  })
})
