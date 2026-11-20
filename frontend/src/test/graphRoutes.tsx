import { render } from '@testing-library/react'
import type { RenderResult } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import type { QueryClient } from '@tanstack/react-query'
import { Link, Outlet, RouterProvider, createMemoryRouter, parsePath } from 'react-router-dom'
import { graphVocabulariesOptions } from '../api/graph/vocabularies'
import { TYPED_ROUTES } from '../features/graph/pages/typedRoutes'
import type { GraphVocabularies } from '../features/graph/types'
import { server } from '../mocks/server'
import { newGraphClient, recorded } from './graphHooks'

/** Rendered beside the routes, so a row can see the shell stay. */
export const TEST_SHELL = 'Test shell'
/** The shell's links: outside `main` and mounted on every route, as the Layout's Navbar is (components/Layout.tsx:23-38). */
export const SHELL_GALLERY_LINK = 'Shell gallery link'
export const SHELL_INSTANCE_LINK = 'Shell instance link'

export interface GraphRouteOptions {
  client?: QueryClient       // default newGraphClient(): retries off
  basename?: string          // the path then starts with it, as App.tsx's BASE_URL does
  state?: unknown            // the first location's router state
  vocabularies?: boolean     // default true: recorded('vocabularies') is in the cache first (J45)
}
export interface GraphRouteRender extends RenderResult {
  router: ReturnType<typeof createMemoryRouter>
  client: QueryClient
}

/** The route table under a data router (renderWithProviders' MemoryRouter cannot run it). A reload is unmount(), then a new call. */
export function renderGraphRoute(path: string, options: GraphRouteOptions = {}): GraphRouteRender {
  const client = options.client ?? newGraphClient()
  if (options.vocabularies !== false) {
    client.setQueryData(graphVocabulariesOptions().queryKey, recorded<GraphVocabularies>('vocabularies').body)
  }
  const shell = (
    <>
      <p>{TEST_SHELL}</p>
      <nav>
        <Link to="/browsers">{SHELL_GALLERY_LINK}</Link>
        <Link to="/browsers/synthetic-regulation">{SHELL_INSTANCE_LINK}</Link>
      </nav>
      <main><Outlet /></main>
    </>
  )
  const router = createMemoryRouter(
    [{ path: '/', element: shell, children: TYPED_ROUTES }],
    { basename: options.basename, initialEntries: [options.state === undefined ? path : { ...parsePath(path), state: options.state }] },
  )
  const result = render(<QueryClientProvider client={client}><RouterProvider router={router} /></QueryClientProvider>)
  return {
    ...result, router, client,
    unmount: () => {
      result.unmount()
      router.dispose()
    },
  }
}

/** Every answer MSW gives, as `${status} ${path}${query}`: a 501 is its miss (MSW:85-87), which no F4 word prints. */
export function recordAnswers(): { answers: string[]; stop: () => void } {
  const answers: string[] = []
  const answered = ({ request, response }: { request: Request; response: Response }) => {
    const url = new URL(request.url)
    answers.push(`${response.status} ${url.pathname}${url.search}`)
  }
  server.events.on('response:mocked', answered)
  return { answers, stop: () => { server.events.removeListener('response:mocked', answered) } }
}
