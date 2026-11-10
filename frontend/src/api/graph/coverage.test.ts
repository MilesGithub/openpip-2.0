import { renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { demoRefs } from '../../mocks/fixtures/typedGraph/recorded'
import { typedGraphRoutes } from '../../mocks/handlers/typedGraph'
import { GRAPH_WAIT, graphWrapper, recordRequests } from '../../test/graphHooks'
import type { RequestLog } from '../../test/graphHooks'
import * as browsers from './browsers'
import * as entities from './entities'
import * as network from './network'
import * as relationships from './relationships'
import * as resolve from './resolve'
import * as status from './status'
import * as vocabularies from './vocabularies'

type Hooks = typeof browsers & typeof entities & typeof network & typeof relationships & typeof resolve & typeof status & typeof vocabularies
type HookName = Extract<keyof Hooks, `use${string}`>

const CALLS: { [K in HookName]: () => unknown } = {
  useGraphStatus: () => status.useGraphStatus(),
  useGraphFeature: () => status.useGraphFeature('network'),
  useGraphVocabularies: () => vocabularies.useGraphVocabularies(),
  useGraphResolve: () => resolve.useGraphResolve({ q: ['SYN-NOPE'] }),
  useGraphEntity: () => entities.useGraphEntity(demoRefs.tf1),
  useGraphNetwork: () => network.useGraphNetwork({ seeds: [demoRefs.tf1] }),
  useGraphRelationship: () => relationships.useGraphRelationship(demoRefs.mixed),
  useGraphEvidence: () => relationships.useGraphEvidence(demoRefs.mixed),
  useGraphBrowsers: () => browsers.useGraphBrowsers(),
  useGraphBrowser: () => browsers.useGraphBrowser('synthetic-regulation'),
}

let log: RequestLog

beforeEach(() => {
  log = recordRequests()
})

afterEach(() => {
  log.stop()
})

describe('the client against the handlers', () => {
  it('every hook settles, and every route is in use', async () => {
    const rendered = Object.entries(CALLS).map(([name, call]) => ({
      name,
      result: renderHook(() => call(), { wrapper: graphWrapper() }).result,
    }))

    for (const { name, result } of rendered) {
      await waitFor(
        () =>
          expect(
            name === 'useGraphFeature' ? result.current : (result.current as { isSuccess: boolean }).isSuccess,
            name,
          ).toBe(name === 'useGraphFeature' ? 'on' : true),
        GRAPH_WAIT,
      )
    }

    expect(log.unhandled).toEqual([])
    for (const route of typedGraphRoutes.filter((candidate) => !candidate.stream)) {
      const pattern = new RegExp(`^${route.path.replace(/\{\w+\}/g, '[^/]+')}$`)
      expect(log.urls.some((url) => pattern.test(url.pathname)), route.path).toBe(true)
    }
  })
})
