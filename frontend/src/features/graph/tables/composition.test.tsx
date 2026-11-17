import { act, cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { Core } from 'cytoscape'
import { http, HttpResponse } from 'msw'
import { useCallback, useMemo } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { server } from '../../../mocks/server'
import { useAuthStore } from '../../../store/authStore'
import { GRAPH_WAIT, graphWrapper, newGraphClient, recorded, recordRequests } from '../../../test/graphHooks'
import type { RequestLog } from '../../../test/graphHooks'
import type { GraphEntityResponse, GraphNetworkResponse, GraphVocabularies } from '../types'
import { graphOfNetwork } from '../network/graph'
import { TypedNetwork } from '../network/TypedNetwork'
import { TypedNetworkNotice } from '../network/TypedNetworkNotice'
import { defaultShowNonSupporting } from '../network/visibility'
import { TypedSelectionPanel } from '../panels/TypedSelectionPanel'
import { useTypedSelection } from '../panels/selection'
import type { PanelSelection } from '../panels/selection'
import { TypedEdgesTable } from './TypedEdgesTable'

const log = vi.hoisted(() => ({ instances: [], runs: new Map() }))
vi.mock('react-cytoscapejs', async (importOriginal) =>
  (await import('../../../test/headlessCytoscape')).headlessCytoscapeModule(importOriginal, log))

let requests: RequestLog
// What the canvas, the table and the panels hand the page's one handler.
const selections = vi.fn<(selection: PanelSelection | null) => void>()

beforeEach(() => {
  log.instances.length = 0
  log.runs.clear()
  selections.mockReset()
  useAuthStore.getState().logout()
  requests = recordRequests()
  vi.spyOn(console, 'warn')
  vi.spyOn(console, 'error')
})

afterEach(() => {
  // Unmounted first, so that what the unmount reports is checked too.
  cleanup()
  requests.stop()
  const calls = [...vi.mocked(console.warn).mock.calls, ...vi.mocked(console.error).mock.calls]
  vi.restoreAllMocks()
  expect(requests.unhandled).toEqual([])
  expect(calls.map((args) => args.map(String).join(' ').slice(0, 300))).toEqual([])
})

// Words copied from the spec's sections 8.3, 8.10 and 13.2, never from the modules under test.
const CAPTION = 'Relationships in view'
const WAITING = 'This panel waits for the network.'
const PROVISIONAL = "Status: provisional (created from a release's evidence; no reference release has stated this identifier yet)."
const P1_P2 = 'Physical interaction between SYN-P1 (Protein (UniProtKB entry)) and SYN-P2 (Protein (UniProtKB entry))'
const ENCODES = 'Encodes: SYN-TF1 (Gene) to SYN-P1 (Protein (UniProtKB entry))'
const ISOFORM = 'Isoform of: SYN-P1-1 (Protein isoform) to SYN-P1 (Protein (UniProtKB entry))'
const TF1_G1 = 'Transcriptional regulation: SYN-TF1 (Gene) to SYN-G1 (Gene)'
const TF1_G2 = 'Transcriptional regulation: SYN-TF1 (Gene) to SYN-G2 (Gene)'
const P1 = 'SYN-P1 (Protein (UniProtKB entry))'
const TF1 = 'SYN-TF1 (Gene)'

// The harness names every row and region from the recorded vocabularies and never asks for them,
// which would add a request to every row's count.
const vocabularies = recorded<GraphVocabularies>('vocabularies').body
const REL = '/api/graph/v1/relationships/'
const pageRequest = (id: number, search = '') => [`${REL}relationship%3A${id}`, search]
const evidenceRequest = (id: number, search = '') => [`${REL}relationship%3A${id}/evidence`, search]
const entityRequest = (id: number, search = '') => [`/api/graph/v1/entities/entity%3A${id}`, search]
const requested = () => requests.urls.map((url) => [url.pathname, url.search])

/** The one instance alive, so that a row never taps a destroyed one. */
function only(): Core {
  const live = (log.instances as Core[]).filter((cy) => !cy.destroyed())
  expect(live).toHaveLength(1)
  return live[0]
}

/** Inside act, so that the debounce's update and the query's land where React expects them. */
async function pause(ms: number): Promise<void> {
  await act(async () => {
    await new Promise<void>((resolve) => setTimeout(resolve, ms))
  })
}

async function advance(ms: number): Promise<void> {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms)
  })
}

/** Section 15's composition over a recorded network; a null name is a failed or disabled network. */
function Page({ name, show, initial }: { name: string | null; show?: boolean; initial?: PanelSelection | null }) {
  const graph = useMemo(() => (name === null ? null : graphOfNetwork(recorded<GraphNetworkResponse>(name).body)), [name])
  const shown = show ?? defaultShowNonSupporting(graph)
  const { selected, select } = useTypedSelection(graph, { showNonSupporting: shown, initial })
  const onSelect = useCallback((selection: PanelSelection | null) => {
    selections(selection)
    select(selection)
  }, [select])
  return (
    <>
      <TypedNetworkNotice graph={graph} showNonSupporting={shown} />
      <TypedNetwork
        graph={graph}
        state={graph === null ? 'error' : 'ready'}
        vocabularies={vocabularies}
        selected={selected}
        onSelect={onSelect}
        showNonSupporting={shown}
      />
      <TypedEdgesTable graph={graph} vocabularies={vocabularies} selected={selected} onSelect={onSelect} showNonSupporting={shown} />
      {selected !== null && (
        <TypedSelectionPanel selection={selected} graph={graph} vocabularies={vocabularies} onSelect={onSelect} onClose={() => select(null)} />
      )}
    </>
  )
}

function rowButton(name: string): HTMLElement {
  return within(screen.getByRole('table', { name: CAPTION })).getByRole('button', { name })
}

function headingOf(region: HTMLElement): HTMLElement {
  return within(region).getByRole('heading', { level: 2 })
}

/** The relationship page and the evidence list have both answered: the count line shows and the status line is empty. */
async function loaded(region: HTMLElement, count: string): Promise<void> {
  await within(region).findByText(count, {}, GRAPH_WAIT)
  await waitFor(() => expect(within(region).getAllByRole('status')[0].textContent).toBe(''), GRAPH_WAIT)
}

/** The entity page has answered. */
async function entityLoaded(region: HTMLElement): Promise<void> {
  await within(region).findByText(PROVISIONAL, {}, GRAPH_WAIT)
  await waitFor(() => expect(within(region).getAllByRole('status')[0].textContent).toBe(''), GRAPH_WAIT)
}

describe('composition: the canvas, the table and the panel on one page', () => {
  it('CMP-1: a tap and Enter on the row select alike and open one panel named as the row; the reopening reads the cache', async () => {
    const user = userEvent.setup()
    render(<Page name="network-p1-reference" />, { wrapper: graphWrapper(newGraphClient()) })
    act(() => {
      only().$id('relationship:3').emit('tap')
    })
    expect(screen.getAllByRole('region')).toHaveLength(1)
    const region = screen.getByRole('region', { name: P1_P2 })
    expect(rowButton(P1_P2)).toBeInTheDocument()
    expect(document.activeElement).toBe(headingOf(region))
    await loaded(region, '2 of 2 results shown.')
    const text = region.textContent
    expect(document.activeElement).toBe(headingOf(region))
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('region')).toBeNull()

    const row = rowButton(P1_P2)
    row.focus()
    await user.keyboard('{Enter}')
    expect(screen.getAllByRole('region')).toHaveLength(1)
    const again = screen.getByRole('region', { name: P1_P2 })
    expect(document.activeElement).toBe(headingOf(again))
    await within(again).findByText('2 of 2 results shown.', {}, GRAPH_WAIT)
    expect(again.textContent).toBe(text)
    expect(selections.mock.calls.map(([selection]) => selection?.id)).toEqual(['relationship:3', 'relationship:3'])
    expect(selections.mock.calls[1][0]).toEqual(selections.mock.calls[0][0])
    // Within the stale time the second mount reads the cache.
    expect(requested()).toEqual([pageRequest(3), evidenceRequest(3)])
  }, 20_000)

  it('CMP-2: three clicks within the debounce send one page request and one evidence request', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    try {
      const user = userEvent.setup()
      render(<Page name="network-p1-reference" />, { wrapper: graphWrapper(newGraphClient()) })
      const names = [ENCODES, ISOFORM, P1_P2]
      for (const [index, name] of names.entries()) {
        // Each opening moves focus to its panel's heading, so each activation is a click, which focuses its button first.
        await user.click(rowButton(name))
        expect(screen.getAllByRole('region')).toHaveLength(1)
        expect(screen.getByRole('region', { name })).toBeInTheDocument()
        if (index < names.length - 1) await advance(100)
      }
      await advance(400)
      await loaded(screen.getByRole('region', { name: P1_P2 }), '2 of 2 results shown.')
      expect(requested()).toEqual([pageRequest(3), evidenceRequest(3)])
      cleanup()
    } finally {
      vi.useRealTimers()
    }
  }, 20_000)

  it("CMP-3: a selection that leaves the visible graph closes its panel, focus goes to the table's caption, and it stays closed", async () => {
    const user = userEvent.setup()
    const view = render(<Page name="network-tf1-outcome-all" show={true} />, { wrapper: graphWrapper(newGraphClient()) })
    const row = rowButton(TF1_G2)
    row.focus()
    await user.keyboard('{Enter}')
    const region = screen.getByRole('region', { name: TF1_G2 })
    await loaded(region, '1 of 1 result shown.')
    expect(requested()).toEqual([pageRequest(7, '?outcome=all'), evidenceRequest(7)])
    expect(document.activeElement).toBe(headingOf(region))

    view.rerender(<Page name="network-tf1-outcome-all" show={false} />)
    expect(screen.queryByRole('region')).toBeNull()
    expect(screen.queryByRole('button', { name: TF1_G2 })).toBeNull()
    const caption = screen.getByRole('table', { name: CAPTION }).querySelector('caption')
    await waitFor(() => expect(document.activeElement).toBe(caption))

    view.rerender(<Page name="network-tf1-outcome-all" show={true} />)
    await pause(600)
    expect(screen.queryByRole('region')).toBeNull()
    expect(requested()).toHaveLength(2)
  }, 20_000)

  it('CMP-4: an end button opens the entity panel; Escape there returns focus to the row that opened the first panel', async () => {
    const user = userEvent.setup()
    render(<Page name="network-p1-reference" />, { wrapper: graphWrapper(newGraphClient()) })
    const row = rowButton(P1_P2)
    row.focus()
    await user.keyboard('{Enter}')
    const region = screen.getByRole('region', { name: P1_P2 })
    await loaded(region, '2 of 2 results shown.')
    const end = within(region).getByRole('button', { name: P1 })
    end.focus()
    await user.keyboard('{Enter}')
    expect(screen.queryByRole('region', { name: P1_P2 })).toBeNull()
    expect(screen.getAllByRole('region')).toHaveLength(1)
    const entity = screen.getByRole('region', { name: P1 })
    await waitFor(() => expect(document.activeElement).toBe(headingOf(entity)))
    await entityLoaded(entity)
    expect(requested()).toEqual([pageRequest(3), evidenceRequest(3), entityRequest(10)])
    expect(selections.mock.calls.map(([selection]) => selection?.id)).toEqual(['relationship:3', 'entity:10'])

    await user.keyboard('{Escape}')
    expect(screen.queryByRole('region')).toBeNull()
    expect(end.isConnected).toBe(false)
    expect(document.activeElement).toBe(row)
    expect(requested()).toHaveLength(3)
  }, 20_000)

  it("CMP-5: the panel sends the network query's releases, and the evidence list sends them too", async () => {
    const user = userEvent.setup()
    render(<Page name="network-tf1-v1" />, { wrapper: graphWrapper(newGraphClient()) })
    rowButton(TF1_G1).focus()
    await user.keyboard('{Enter}')
    await loaded(screen.getByRole('region', { name: TF1_G1 }), '1 of 1 result shown.')
    expect(requested()).toEqual([pageRequest(6, '?releases=release:1'), evidenceRequest(6, '?releases=release:1')])
  }, 20_000)

  it('CMP-6: a network that turns null leaves an open panel as it was and sends nothing, a relationship and an entity alike', async () => {
    const user = userEvent.setup()
    const view = render(<Page name="network-tf1-v1" />, { wrapper: graphWrapper(newGraphClient()) })
    rowButton(TF1_G1).focus()
    await user.keyboard('{Enter}')
    let region = screen.getByRole('region', { name: TF1_G1 })
    await loaded(region, '1 of 1 result shown.')
    expect(requested()).toEqual([pageRequest(6, '?releases=release:1'), evidenceRequest(6, '?releases=release:1')])
    const words = region.textContent

    view.rerender(<Page name={null} />)
    await pause(1000)
    expect(screen.queryByRole('button', { name: TF1_G1 })).toBeNull()
    region = screen.getByRole('region', { name: TF1_G1 })
    expect(region.textContent).toBe(words)
    expect(requested()).toHaveLength(2)

    view.rerender(<Page name="network-tf1-v1" />)
    await pause(1000)
    region = screen.getByRole('region', { name: TF1_G1 })
    expect(region.textContent).toBe(words)
    expect(requested()).toHaveLength(2)
    view.unmount()

    // No recording holds entity:7 under release:1.
    server.use(http.get('/api/graph/v1/entities/:id', () => HttpResponse.json(recorded<GraphEntityResponse>('entity-tf1').body)))
    const entityView = render(<Page name="network-tf1-v1" />, { wrapper: graphWrapper(newGraphClient()) })
    act(() => {
      only().$id('entity:7').emit('tap')
    })
    let entity = screen.getByRole('region', { name: TF1 })
    await entityLoaded(entity)
    expect(requested()).toHaveLength(3)
    const entityRequests = () => requested().filter(([path]) => path.startsWith('/api/graph/v1/entities/'))
    expect(entityRequests()).toEqual([entityRequest(7, '?releases=release:1')])
    const entityWords = entity.textContent

    entityView.rerender(<Page name={null} />)
    await pause(1000)
    entity = screen.getByRole('region', { name: TF1 })
    expect(entity.textContent).toBe(entityWords)
    expect(entityRequests()).toHaveLength(1)

    entityView.rerender(<Page name="network-tf1-v1" />)
    await pause(1000)
    entity = screen.getByRole('region', { name: TF1 })
    expect(entity.textContent).toBe(entityWords)
    expect(entityRequests()).toHaveLength(1)
  }, 20_000)

  it('CMP-7: a restored selection waits for the first graph, then sends one page request and one evidence request', async () => {
    const initial: PanelSelection = { kind: 'edge', id: 'relationship:6' }
    const view = render(<Page name={null} initial={initial} />, { wrapper: graphWrapper(newGraphClient()) })
    const region = screen.getByRole('region', { name: 'Relationship relationship:6' })
    expect(within(region).getByText(WAITING)).toBeInTheDocument()
    await pause(1000)
    expect(within(region).getByText(WAITING)).toBeInTheDocument()
    expect(requested()).toEqual([])

    view.rerender(<Page name="network-tf1-v1" initial={initial} />)
    expect(screen.getAllByRole('region')).toHaveLength(1)
    const named = screen.getByRole('region', { name: TF1_G1 })
    await loaded(named, '1 of 1 result shown.')
    expect(within(named).queryByText(WAITING)).toBeNull()
    expect(requested()).toEqual([pageRequest(6, '?releases=release:1'), evidenceRequest(6, '?releases=release:1')])
  }, 20_000)

  it('CMP-8: a background tap after a pointer has taken focus closes the panel and leaves focus where the pointer put it', async () => {
    const user = userEvent.setup()
    render(<Page name="network-p1-reference" />, { wrapper: graphWrapper(newGraphClient()) })
    const row = rowButton(P1_P2)
    row.focus()
    await user.keyboard('{Enter}')
    const region = screen.getByRole('region', { name: P1_P2 })
    await loaded(region, '2 of 2 results shown.')
    // A canvas mousedown leaves focus on the page's body.
    const focused = document.activeElement as HTMLElement
    expect(focused).toBe(headingOf(region))
    focused.blur()
    expect(document.activeElement).toBe(document.body)
    act(() => {
      only().emit('tap')
    })
    expect(screen.queryByRole('region')).toBeNull()
    await pause(50)
    expect(document.activeElement).not.toBe(row)
    expect(document.activeElement).toBe(document.body)
    expect(selections.mock.calls.map(([selection]) => selection?.id ?? null)).toEqual(['relationship:3', null])
    expect(requested()).toEqual([pageRequest(3), evidenceRequest(3)])
  }, 20_000)
})
