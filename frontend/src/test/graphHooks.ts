import { createElement } from 'react'
import type { ReactNode } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { recordedEntries } from '../mocks/fixtures/typedGraph/index'
import type { RecordedEntry } from '../mocks/fixtures/typedGraph/recorded'
import { server } from '../mocks/server'

/** For a waitFor on a typed request: the first of a file loads the fixtures. */
export const GRAPH_WAIT = { timeout: 3000 }

export function newGraphClient(): QueryClient {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } })
}

export function graphWrapper(client: QueryClient = newGraphClient()) {
  return function GraphWrapper({ children }: { children: ReactNode }) {
    return createElement(QueryClientProvider, { client }, children)
  }
}

export function recorded<TBody>(name: string): RecordedEntry<TBody> {
  const entry = recordedEntries.find((candidate) => candidate.name === name)
  if (!entry) throw new Error(`No recorded fixture named ${name}`)
  return entry as RecordedEntry<TBody>
}

export interface RequestLog {
  urls: URL[]
  unhandled: string[]
  stop: () => void
}

/** The URLs requested, and any request no handler matched: `setup.ts` only warns about those. */
export function recordRequests(): RequestLog {
  const log: RequestLog = { urls: [], unhandled: [], stop: () => {} }
  const started = ({ request }: { request: Request }) => {
    log.urls.push(new URL(request.url))
  }
  const missed = ({ request }: { request: Request }) => {
    log.unhandled.push(`${request.method} ${request.url}`)
  }
  server.events.on('request:start', started)
  server.events.on('request:unhandled', missed)
  log.stop = () => {
    server.events.removeListener('request:start', started)
    server.events.removeListener('request:unhandled', missed)
  }
  return log
}
