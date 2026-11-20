import { cleanup, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useAuthStore } from '../../store/authStore'
import { recordRequests } from '../../test/graphHooks'
import type { RequestLog } from '../../test/graphHooks'
import { TEST_SHELL, recordAnswers, renderGraphRoute } from '../../test/graphRoutes'
import { reloadDocument } from '../graph/pages/reloadDocument'

// A file of its own, since a module mock holds for its whole file: the instance page's chunk fails as a stale
// one does after a deploy, and jsdom's location.reload cannot be spied on, so its seam is mocked. Nothing is
// warmed up, since the mocked page cannot be imported.
vi.mock('./InstancePage', () => {
  throw new Error('Failed to fetch dynamically imported module')
})
vi.mock('../graph/pages/reloadDocument', () => ({ reloadDocument: vi.fn() }))

let requests: RequestLog
let answers: { answers: string[]; stop: () => void }

beforeEach(() => {
  useAuthStore.getState().logout()
  requests = recordRequests()
  answers = recordAnswers()
  vi.spyOn(console, 'warn')
  vi.spyOn(console, 'error')
})

afterEach(() => {
  // Unmounted first, so that what the unmount reports is checked too.
  cleanup()
  requests.stop()
  answers.stop()
  const calls = [...vi.mocked(console.warn).mock.calls, ...vi.mocked(console.error).mock.calls]
  vi.restoreAllMocks()
  expect(requests.unhandled).toEqual([])
  expect(answers.answers.filter((answer) => answer.startsWith('501 '))).toEqual([])
  expect(calls.map((args) => args.map(String).join(' ').slice(0, 300))).toEqual([])
})

// Words copied from the spec's section 3.1, never from the modules under test.
const PAGE_UNKNOWN = 'This page could not be loaded.'
const RELOAD = 'Reload'
const PAGE_LOADING = 'Loading this page…'

const asked = () => requests.urls.map((url) => url.pathname + url.search)

describe("the routes' error boundary", () => {
  it('RE-1: a page chunk that fails to load shows the Reload boundary inside the shell', async () => {
    const user = userEvent.setup()
    renderGraphRoute('/browsers/synthetic-regulation')
    expect(await screen.findByRole('heading', { level: 1, name: PAGE_UNKNOWN }, { timeout: 10_000 })).toBeInTheDocument()
    const reload = screen.getByRole('button', { name: RELOAD })
    expect(screen.getByText(TEST_SHELL)).toBeInTheDocument()
    await user.click(reload)
    expect(vi.mocked(reloadDocument)).toHaveBeenCalledTimes(1)
    expect(screen.queryByText(PAGE_LOADING)).toBeNull()
    // The gate's one read; the page that failed to load sent nothing.
    expect(asked()).toEqual(['/api/graph/v1/status'])
    // The allowance (spec 12.0): React's caught-error report, then React Router's, asserted here and cleared,
    // so any other call fails the row.
    const errors = vi.mocked(console.error).mock.calls.map((args) => String(args[0]))
    expect(errors).toHaveLength(2)
    expect(errors[0].startsWith('%o\n\n%s\n\n%s\n')).toBe(true)
    expect(errors[1].startsWith('React Router caught the following error during render')).toBe(true)
    vi.mocked(console.error).mockClear()
  }, 20_000)
})
