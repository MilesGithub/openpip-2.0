import { QueryObserver, onlineManager } from '@tanstack/react-query'
import { act, cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { useCallback, useMemo } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  activeEntityPage, hostile, hostileEntityPage, mergedEntityPage, nullStatusEntityPage, retiredEntityPage, unknownStatusEntityPage,
} from '../../../mocks/fixtures/typedGraph/extras'
import { server } from '../../../mocks/server'
import { useAuthStore } from '../../../store/authStore'
import { GRAPH_WAIT, graphWrapper, newGraphClient, recorded, recordRequests } from '../../../test/graphHooks'
import type { RequestLog } from '../../../test/graphHooks'
import type { GraphEntityResponse, GraphNetworkResponse, GraphVocabularies } from '../types'
import { EMPTY_GRAPH, graphOfNetwork } from '../network/graph'
import type { TypedGraph } from '../network/graph'
import { defaultShowNonSupporting } from '../network/visibility'
import { edgeDescription } from '../network/words'
import { EntityPanel } from './EntityPanel'
import { useTypedSelection } from './selection'
import type { PanelSelection } from './selection'
import { TypedSelectionPanel } from './TypedSelectionPanel'

let log: RequestLog

beforeEach(() => {
  useAuthStore.getState().logout()
  log = recordRequests()
  vi.spyOn(console, 'warn')
  vi.spyOn(console, 'error')
})

afterEach(() => {
  // Unmounted first, so that what the unmount reports is checked too.
  cleanup()
  onlineManager.setOnline(true)
  log.stop()
  const calls = [...vi.mocked(console.warn).mock.calls, ...vi.mocked(console.error).mock.calls]
  vi.restoreAllMocks()
  expect(log.unhandled).toEqual([])
  expect(calls.map((args) => args.map(String).join(' ').slice(0, 300))).toEqual([])
})

// Words copied from the spec's sections 8.1, 8.2 and 8.10, never from the module under test.
const SYNTHETIC = 'Synthetic demonstration data: made up to demonstrate openPIP; it describes no real biology.'
const PROVISIONAL = "Status: provisional (created from a release's evidence; no reference release has stated this identifier yet)."
const COUNTS_NOTE = 'Relationships with supporting evidence in the selected releases, before any evidence filter:'
const NO_REFERENCES = 'No reference relationship with supporting evidence in the selected releases.'
const LOADING = 'Loading this entity…'
const TRY_AGAIN = 'Try loading this entity again'
const NOT_AVAILABLE = 'This entity is not available to you.'
const BAD_BODY = "The server's answer could not be read."

const ENT = '/api/graph/v1/entities/'
const REL = '/api/graph/v1/relationships/'
const entityRequest = (id: number, search = '') => [`${ENT}entity%3A${id}`, search]
const requests = () => log.urls.map((url) => [url.pathname, url.search])

const vocabularies = () => recorded<GraphVocabularies>('vocabularies').body
const network = (name: string): TypedGraph => graphOfNetwork(recorded<GraphNetworkResponse>(name).body)

function openEntity(ref: string) {
  return render(<EntityPanel entityRef={ref} selection={{}} vocabularies={vocabularies()} onClose={() => {}} />,
    { wrapper: graphWrapper(newGraphClient()) })
}

function stubEntity(body: GraphEntityResponse) {
  server.use(http.get(`${ENT}:id`, () => HttpResponse.json(body)))
}

/** 16.0, requests in flight: a held request is answered only once the row has read its in-flight state. */
function holdRequests() {
  const resolvers: (() => void)[] = []
  return {
    wait: () => new Promise<void>((resolve) => {
      resolvers.push(resolve)
    }),
    release: () => {
      for (const resolve of resolvers.splice(0)) resolve()
    },
    waiting: () => resolvers.length,
  }
}

/** Inside act, so that what settles meanwhile lands where React expects it. */
async function pause(ms: number): Promise<void> {
  await act(async () => {
    await new Promise<void>((resolve) => setTimeout(resolve, ms))
  })
}

/** The body's one status line: the entity panel holds no other region. */
function statusLine(region: HTMLElement): HTMLElement {
  const lines = within(region).getAllByRole('status')
  expect(lines).toHaveLength(1)
  return lines[0]
}

/** Section 15's composition without the canvas: the entity selection restored over network-tf1, and one handler for every panel. */
function PageHarness({ spy }: { spy: (selection: PanelSelection | null) => void }) {
  const graph = useMemo(() => network('network-tf1'), [])
  const { selected, select } = useTypedSelection(graph, {
    showNonSupporting: defaultShowNonSupporting(graph), initial: { kind: 'node', id: 'entity:7' },
  })
  const choose = useCallback((selection: PanelSelection | null) => {
    spy(selection)
    select(selection)
  }, [spy, select])
  return selected === null ? null : (
    <TypedSelectionPanel selection={selected} graph={graph} vocabularies={vocabularies()} onSelect={choose} onClose={() => select(null)} />
  )
}

describe('EntityPanel', () => {
  it('EP-1: entity-tf1 from network-tf1: its status and taxon in words; every other status from stubs', async () => {
    const graph = network('network-tf1')
    const node = graph.nodes.find((candidate) => candidate.id === 'entity:7')
    if (node === undefined) throw new Error('no entity:7 in network-tf1')
    render(<TypedSelectionPanel selection={{ kind: 'node', id: 'entity:7', record: node }} graph={graph} vocabularies={vocabularies()} onClose={() => {}} />,
      { wrapper: graphWrapper(newGraphClient()) })
    const region = screen.getByRole('region', { name: 'SYN-TF1 (Gene)' })
    await within(region).findByText(PROVISIONAL, {}, GRAPH_WAIT)
    expect(within(region).getByText('Taxon: synthetic construct (taxid 32630)')).toBeInTheDocument()
    expect(requests()).toEqual([entityRequest(7)])
    cleanup()
    const stubs: [GraphEntityResponse, string][] = [
      [activeEntityPage, 'Status: active (a reference release states this identifier).'],
      [mergedEntityPage, 'Status: merged (merged into another record).'],
      [retiredEntityPage, 'Status: retired (retired, kept for the record).'],
      [nullStatusEntityPage, 'Status not given.'],
      [unknownStatusEntityPage, 'Status: syn status.'],
    ]
    for (const [body, words] of stubs) {
      stubEntity(body)
      const view = openEntity('entity:901')
      const stubbed = screen.getByRole('region', { name: 'Entity entity:901' })
      await within(stubbed).findByText(words, {}, GRAPH_WAIT)
      expect(within(stubbed).getByText(NO_REFERENCES)).toBeInTheDocument()
      view.unmount()
      server.resetHandlers()
    }
    expect(requests()).toEqual([entityRequest(7), ...stubs.map(() => entityRequest(901))])
  }, 20_000)

  it("EP-2: a followed merge: the survivor is named beside the ref asked for", async () => {
    const survivor = recorded<GraphEntityResponse>('entity-tf1').body
    server.use(http.get(`${ENT}:id`, ({ params }) => (
      decodeURIComponent(String(params.id)) === 'entity:98' ? HttpResponse.json(survivor) : undefined
    )))
    const graph = network('network-tf1')
    render(<TypedSelectionPanel selection={{ kind: 'node', id: 'entity:98' }} graph={graph} vocabularies={vocabularies()} onClose={() => {}} />,
      { wrapper: graphWrapper(newGraphClient()) })
    const region = screen.getByRole('region', { name: 'Entity entity:98' })
    await within(region).findByText('The entity asked for, entity:98, was merged into this one.', {}, GRAPH_WAIT)
    expect(within(region).getByText('SYN-TF1 (Gene)')).toBeInTheDocument()
    expect(within(region).getByRole('heading', { level: 2 }).textContent).toBe('Entity entity:98')
    expect(requests()).toEqual([entityRequest(98)])
  }, 20_000)

  it('EP-3: the relationship counts, worded as the entity page counts them', async () => {
    openEntity('entity:7')
    const region = screen.getByRole('region')
    await within(region).findByText(COUNTS_NOTE, {}, GRAPH_WAIT)
    expect(within(region).getByText('Encodes: 1')).toBeInTheDocument()
    expect(within(region).getByText('Transcriptional regulation: 3')).toBeInTheDocument()
    expect(within(region).getByRole('heading', { level: 3, name: 'Relationships by type' })).toBeInTheDocument()
    expect(requests()).toEqual([entityRequest(7)])
  }, 20_000)

  it("EP-4: a reference relationship opens with the entity page's selection and nodes, named and worded as its button", async () => {
    const user = userEvent.setup()
    const onSelect = vi.fn()
    render(<PageHarness spy={onSelect} />, { wrapper: graphWrapper(newGraphClient()) })
    const entityRegion = screen.getByRole('region', { name: 'SYN-TF1 (Gene)' })
    const name = 'Encodes: SYN-TF1 (Gene) to SYN-P1 (Protein (UniProtKB entry))'
    const button = await within(entityRegion).findByRole('button', { name }, GRAPH_WAIT)
    const entityPage = recorded<GraphEntityResponse>('entity-tf1').body
    await user.click(button)
    expect(onSelect).toHaveBeenCalledTimes(1)
    expect(onSelect).toHaveBeenCalledWith({
      kind: 'edge', id: 'relationship:1', record: entityPage.reference_relationships[0], params: {}, nodes: entityPage.nodes,
    })
    expect(entityPage.nodes.map((node) => node.id)).toEqual(['entity:7', 'entity:10'])
    const entityGraph: TypedGraph = {
      ...EMPTY_GRAPH, nodes: entityPage.nodes, edges: entityPage.reference_relationships, releases: entityPage.releases,
    }
    const sentence = edgeDescription(entityPage.reference_relationships[0], entityGraph, null, vocabularies())
    const region = screen.getByRole('region', { name })
    const sentences = () => Array.from(region.querySelectorAll('p')).filter((paragraph) => paragraph.textContent === sentence)
    // While it waits, the sentence is already the one it shows after load.
    expect(within(region).getByText('Loading this relationship…')).toBeInTheDocument()
    expect(sentences()).toHaveLength(1)
    await within(region).findByText('1 of 1 result shown.', {}, GRAPH_WAIT)
    await waitFor(() => expect(within(region).getAllByRole('status')[0].textContent).toBe(''), GRAPH_WAIT)
    expect(screen.getByRole('region', { name })).toBe(region)
    expect(sentences()).toHaveLength(1)
    expect(requests()).toEqual([entityRequest(7), [`${REL}relationship%3A1`, ''], [`${REL}relationship%3A1/evidence`, '']])
  }, 20_000)

  it('EP-5: the identifiers table: a version, the role labels, a link only for an http(s) address; the description', async () => {
    stubEntity(activeEntityPage)
    openEntity('entity:901')
    const region = screen.getByRole('region')
    const table = await within(region).findByRole('table', { name: 'Identifiers' }, GRAPH_WAIT)
    const rows = within(table).getAllByRole('row').slice(1)
    expect(rows.map((row) => Array.from(row.children).map((cell) => cell.textContent))).toEqual([
      ['Synthetic gene ID', 'SYN-N901', '', 'primary', ''],
      ['Synthetic gene ID', 'SYN-N901-ALT', '2', 'secondary', 'https://example.org/entity/SYN-N901'],
      ['Synthetic gene ID', 'SYN-N901-SYM', '', 'symbol', ''],
    ])
    expect(rows.map((row) => row.children[1].tagName)).toEqual(['TH', 'TH', 'TH'])
    const links = within(table).getAllByRole('link')
    expect(links).toHaveLength(1)
    expect(links[0].getAttribute('href')).toBe('https://example.org/entity/SYN-N901')
    expect(links[0].getAttribute('target')).toBe('_blank')
    expect(links[0].getAttribute('rel')).toBe('noopener noreferrer')
    expect(within(region).getByText('A hand-written test gene.')).toBeInTheDocument()
    expect(requests()).toEqual([entityRequest(901)])
  }, 20_000)

  it("EP-6: the API's 404 never says the entity does not exist and offers no Try again; an unrecorded entity is the mirror's 501, which offers one", async () => {
    server.use(http.get('/api/graph/v1/entities/:id', () => HttpResponse.json({ detail: 'Not found.' }, { status: 404 })))
    const view = openEntity('entity:7')
    let region = screen.getByRole('region')
    await within(region).findByText(NOT_AVAILABLE, {}, GRAPH_WAIT)
    for (const banned of [/not enabled/i, /does not exist/i, /Not found\./]) expect(region.textContent).not.toMatch(banned)
    // A repeat at the user's hand would spend the allowance and read alike, so not_found offers none.
    expect(within(region).queryByRole('button', { name: TRY_AGAIN })).toBeNull()
    view.unmount()
    server.resetHandlers()
    openEntity('entity:1')
    region = screen.getByRole('region')
    await within(region).findByText('This entity could not be loaded (HTTP 501).', {}, GRAPH_WAIT)
    expect(within(region).getByRole('button', { name: TRY_AGAIN })).toBeInTheDocument()
    expect(requests()).toEqual([entityRequest(7), entityRequest(1)])
  }, 20_000)

  it('EP-7: the synthetic sentence, and every hostile field of an entity page as text', async () => {
    const view = openEntity('entity:7')
    await within(screen.getByRole('region')).findByText(SYNTHETIC, {}, GRAPH_WAIT)
    view.unmount()
    stubEntity(hostileEntityPage)
    const hostileView = openEntity('entity:901')
    const region = screen.getByRole('region')
    const table = await within(region).findByRole('table', { name: 'Identifiers' }, GRAPH_WAIT)
    // jsdom fetches no image and runs no inserted script, so a parsed element is the only proof.
    expect(hostileView.container.querySelector('[data-hostile]')).toBeNull()
    const text = hostileView.container.textContent ?? ''
    const paths = ['entity.label', 'description', 'identifiers.value', 'identifiers.version', 'relationshipCounts.key', 'nodes[1].label']
    expect(paths.filter((path) => !text.includes(hostile(path)))).toEqual([])
    const cells = Array.from(within(table).getAllByRole('row')[1].children)
    expect(cells[cells.length - 1].textContent).toBe('javascript:alert(1)')
    expect(hostileView.container.querySelectorAll('a')).toHaveLength(0)
    expect(requests()).toEqual([entityRequest(7), entityRequest(901)])
  }, 20_000)

  it("EP-8: an entity page's own body: an h1 that names the entity, no Close, and Escape left to the page", async () => {
    const user = userEvent.setup()
    const reachedPage = vi.fn()
    const listen = (event: KeyboardEvent) => {
      if (event.key === 'Escape') reachedPage()
    }
    document.addEventListener('keydown', listen)
    try {
      const node = recorded<GraphEntityResponse>('entity-tf1').body.entity
      render(<EntityPanel entityRef="entity:7" selection={{}} node={node} vocabularies={vocabularies()} headingLevel={1} />,
        { wrapper: graphWrapper(newGraphClient()) })
      const region = screen.getByRole('region', { name: 'SYN-TF1 (Gene)' })
      const heading = within(region).getByRole('heading', { level: 1, name: 'SYN-TF1 (Gene)' })
      await waitFor(() => expect(document.activeElement).toBe(heading))
      expect(within(region).queryByRole('button', { name: 'Close' })).toBeNull()
      // With nothing to close, Escape is left to the page.
      await user.keyboard('{Escape}')
      expect(reachedPage).toHaveBeenCalledTimes(1)
      expect(region.isConnected).toBe(true)
      await within(region).findByText(PROVISIONAL, {}, GRAPH_WAIT)
      expect(within(region).getAllByRole('heading', { level: 2 }).map((element) => element.textContent)).toEqual(
        expect.arrayContaining(['Identifiers', 'Relationships by type', 'Reference relationships', 'Releases']),
      )
    } finally {
      document.removeEventListener('keydown', listen)
    }
    expect(requests()).toEqual([entityRequest(7)])
  }, 20_000)

  it('EP-9: a 503 offers Try again, which stays mounted, aria-disabled and focused while its retry runs, and comes back when it fails', async () => {
    const user = userEvent.setup()
    const hold = holdRequests()
    let calls = 0
    server.use(http.get(`${ENT}:id`, async () => {
      calls += 1
      if (calls === 2) await hold.wait()
      return HttpResponse.json({ detail: 'Too expensive.' }, { status: 503 })
    }))
    const tooExpensive = 'This entity could not be loaded now. Try again later.'
    openEntity('entity:7')
    const region = screen.getByRole('region')
    await within(region).findByText(tooExpensive, {}, GRAPH_WAIT)
    const button = within(region).getByRole('button', { name: TRY_AGAIN })
    await user.click(button)
    // The retry is held: the same button stays, aria-disabled, with focus, beside the loading words.
    await waitFor(() => expect(hold.waiting()).toBe(1), GRAPH_WAIT)
    expect(calls).toBe(2)
    await waitFor(() => expect(button.getAttribute('aria-disabled')).toBe('true'), GRAPH_WAIT)
    expect(within(region).getByRole('button', { name: TRY_AGAIN })).toBe(button)
    expect(button.hasAttribute('disabled')).toBe(false)
    expect(document.activeElement).toBe(button)
    expect(statusLine(region).textContent).toBe(LOADING)
    hold.release()
    await waitFor(() => expect(statusLine(region).textContent).toBe(tooExpensive), GRAPH_WAIT)
    expect(within(region).getByRole('button', { name: TRY_AGAIN })).toBe(button)
    expect(button.getAttribute('aria-disabled')).toBeNull()
    expect(document.activeElement).toBe(button)
    expect(requests()).toEqual([entityRequest(7), entityRequest(7)])
  }, 20_000)

  it("EP-10: a reconnect's refetch replaces a focused Try again never pressed, and after a settled retry no busy Try again follows", async () => {
    const user = userEvent.setup()
    const hold = holdRequests()
    let calls = 0
    // The two reconnects' refetches, the second and the fourth entity requests, are held.
    server.use(http.get(`${ENT}:id`, async () => {
      calls += 1
      const call = calls
      if (call === 2 || call === 4) await hold.wait()
      return call <= 2
        ? HttpResponse.json({ detail: 'Request was throttled.' }, { status: 429 })
        : HttpResponse.json({ detail: 'Not found.' }, { status: 404 })
    }))
    const throttled = 'Too many requests. Try again shortly.'
    openEntity('entity:7')
    const region = screen.getByRole('region')
    const heading = within(region).getByRole('heading', { level: 2 })
    const offeredNow = () => within(region).queryByRole('button', { name: TRY_AGAIN })
    await within(region).findByText(throttled, {}, GRAPH_WAIT)
    const offered = within(region).getByRole('button', { name: TRY_AGAIN })
    offered.focus()
    expect(document.activeElement).toBe(offered)
    // Offline, then online: the reconnect's refetch. Its Try again goes and focus moves to the heading.
    act(() => {
      onlineManager.setOnline(false)
    })
    await act(async () => {
      onlineManager.setOnline(true)
    })
    await waitFor(() => expect(hold.waiting()).toBe(1), GRAPH_WAIT)
    await waitFor(() => expect(statusLine(region).textContent).toBe(LOADING), GRAPH_WAIT)
    await waitFor(() => expect(document.activeElement).toBe(heading), GRAPH_WAIT)
    expect(offeredNow()).toBeNull()
    hold.release()
    await waitFor(() => expect(statusLine(region).textContent).toBe(throttled), GRAPH_WAIT)
    const back = within(region).getByRole('button', { name: TRY_AGAIN })
    expect(back.getAttribute('aria-disabled')).toBeNull()
    expect(document.activeElement).toBe(heading)
    // One press, its retry answered with a 404, which offers no Try again.
    await user.click(back)
    await waitFor(() => expect(statusLine(region).textContent).toBe(NOT_AVAILABLE), GRAPH_WAIT)
    await waitFor(() => expect(document.activeElement).toBe(heading), GRAPH_WAIT)
    expect(offeredNow()).toBeNull()
    // Offline and online again: the 404 is sent again, and reads as a first load, since the retry settled.
    act(() => {
      onlineManager.setOnline(false)
    })
    await act(async () => {
      onlineManager.setOnline(true)
    })
    await waitFor(() => expect(hold.waiting()).toBe(1), GRAPH_WAIT)
    await waitFor(() => expect(statusLine(region).textContent).toBe(LOADING), GRAPH_WAIT)
    expect(offeredNow()).toBeNull()
    hold.release()
    await waitFor(() => expect(statusLine(region).textContent).toBe(NOT_AVAILABLE), GRAPH_WAIT)
    expect(offeredNow()).toBeNull()
    expect(requests()).toEqual([entityRequest(7), entityRequest(7), entityRequest(7), entityRequest(7)])
  }, 20_000)

  it("EP-11: a re-read that fails over a loaded entity page states its failure over the page, its Try again busy while it runs, offline too; a 404's offers none, and a bad body comes first", async () => {
    const user = userEvent.setup()
    // Before the mount: the observer binds the method in its constructor, and the result hands out that copy.
    const refetch = vi.spyOn(QueryObserver.prototype, 'refetch')
    const realNow = Date.now.bind(Date)
    const now = vi.spyOn(Date, 'now')
    const SERVER_ERROR = 'This entity could not be loaded (HTTP 500).'
    type Reply = () => Response | Promise<Response>
    /** entity:7 loaded on a client of its own, then reopened after GRAPH_DATA_STALE_MS: its re-reads take `replies` in turn. */
    const reopenedOver = async (replies: Reply[]) => {
      now.mockImplementation(() => realNow())
      const client = newGraphClient()
      const Wrapper = graphWrapper(client)
      const panel = (open: boolean) => (
        <Wrapper>
          {open && <EntityPanel entityRef="entity:7" selection={{}} vocabularies={vocabularies()} onClose={() => {}} />}
        </Wrapper>
      )
      const start = log.urls.length
      const view = render(panel(true))
      await within(screen.getByRole('region')).findByText(PROVISIONAL, {}, GRAPH_WAIT)
      expect(statusLine(screen.getByRole('region')).textContent).toBe('')
      let calls = 0
      server.use(http.get(`${ENT}:id`, async () => {
        const reply = replies[Math.min(calls, replies.length - 1)]
        calls += 1
        return reply()
      }))
      view.rerender(panel(false))
      now.mockImplementation(() => realNow() + 61_000)
      view.rerender(panel(true))
      const settled = async (count: number) => {
        await waitFor(() => {
          const state = client.getQueryCache().findAll({ queryKey: ['graph', 'entity', 'entity:7'] })[0]?.state
          expect([calls, state?.fetchStatus]).toEqual([count, 'idle'])
        }, GRAPH_WAIT)
        await pause(50)
      }
      return { region: screen.getByRole('region'), calls: () => calls, settled, sent: () => requests().slice(start) }
    }
    const reconnect = async () => {
      act(() => {
        onlineManager.setOnline(false)
      })
      await act(async () => {
        onlineManager.setOnline(true)
      })
    }
    const entityPage = recorded<GraphEntityResponse>('entity-tf1').body
    const hold = holdRequests()
    const failed: Reply = () => HttpResponse.json({ detail: 'A server error occurred.' }, { status: 500 })
    const loaded: Reply = () => HttpResponse.json(entityPage)

    // A JSON 500 offers Try again; held, its re-read keeps the same button, aria-disabled, with focus, beside the same words.
    const first = await reopenedOver([failed, async () => {
      await hold.wait()
      return failed()
    }, async () => {
      await hold.wait()
      return loaded()
    }, failed, loaded])
    let region = first.region
    const heading = within(region).getByRole('heading', { level: 2 })
    await waitFor(() => expect(statusLine(region).textContent).toBe(SERVER_ERROR), GRAPH_WAIT)
    expect(within(region).getByText(PROVISIONAL)).toBeInTheDocument()
    const offered = within(region).getAllByRole('button', { name: TRY_AGAIN })
    expect(offered).toHaveLength(1)
    const button = offered[0]
    expect(button.getAttribute('aria-disabled')).toBeNull()
    // A reconnect re-reads the stale page at once: a re-read no press started, which a press would cancel
    // and send again, so the button is busy through it and keeps focus.
    button.focus()
    await reconnect()
    await waitFor(() => expect(hold.waiting()).toBe(1), GRAPH_WAIT)
    expect(first.calls()).toBe(2)
    await waitFor(() => expect(button.getAttribute('aria-disabled')).toBe('true'), GRAPH_WAIT)
    expect(within(region).getByRole('button', { name: TRY_AGAIN })).toBe(button)
    expect(button.style.cursor).toBe('default')
    expect(document.activeElement).toBe(button)
    expect(statusLine(region).textContent).toBe(SERVER_ERROR)
    expect(within(region).getByText(PROVISIONAL)).toBeInTheDocument()
    const reconnected = refetch.mock.calls.length
    await user.keyboard('{Enter}')
    await pause(300)
    expect(refetch).toHaveBeenCalledTimes(reconnected)
    expect(first.calls()).toBe(2)
    // Refused again, the re-read leaves the failure standing, so the same button is live again, with focus.
    hold.release()
    await waitFor(() => expect(button.getAttribute('aria-disabled')).toBeNull(), GRAPH_WAIT)
    expect(within(region).getByRole('button', { name: TRY_AGAIN })).toBe(button)
    expect(document.activeElement).toBe(button)
    expect(statusLine(region).textContent).toBe(SERVER_ERROR)
    await user.click(button)
    await waitFor(() => expect(hold.waiting()).toBe(1), GRAPH_WAIT)
    expect(first.calls()).toBe(3)
    await waitFor(() => expect(button.getAttribute('aria-disabled')).toBe('true'), GRAPH_WAIT)
    expect(within(region).getByRole('button', { name: TRY_AGAIN })).toBe(button)
    expect(button.hasAttribute('disabled')).toBe(false)
    expect(document.activeElement).toBe(button)
    expect(statusLine(region).textContent).toBe(SERVER_ERROR)
    expect(within(region).getByText(PROVISIONAL)).toBeInTheDocument()
    hold.release()
    await waitFor(() => expect(statusLine(region).textContent).toBe(''), GRAPH_WAIT)
    await waitFor(() => expect(button.isConnected).toBe(false), GRAPH_WAIT)
    await waitFor(() => expect(document.activeElement).toBe(heading), GRAPH_WAIT)

    // A reconnect once the page is stale again re-reads it, refused again; then a press offline waits for the connection.
    now.mockImplementation(() => realNow() + 122_000)
    await reconnect()
    await waitFor(() => expect(statusLine(region).textContent).toBe(SERVER_ERROR), GRAPH_WAIT)
    await pause(50)
    const again = within(region).getByRole('button', { name: TRY_AGAIN })
    expect(again.getAttribute('aria-disabled')).toBeNull()
    again.focus()
    act(() => {
      onlineManager.setOnline(false)
    })
    const pressed = refetch.mock.calls.length
    await user.keyboard('{Enter}')
    await pause(300)
    expect(again.getAttribute('aria-disabled')).toBe('true')
    expect(again.style.cursor).toBe('default')
    expect(document.activeElement).toBe(again)
    expect(statusLine(region).textContent).toBe(SERVER_ERROR)
    expect(refetch).toHaveBeenCalledTimes(pressed + 1)
    expect(first.calls()).toBe(4)
    // A second press would cancel the waiting re-read and start another: it calls nothing.
    await user.keyboard('{Enter}')
    await pause(300)
    expect(refetch).toHaveBeenCalledTimes(pressed + 1)
    await act(async () => {
      onlineManager.setOnline(true)
    })
    await waitFor(() => expect(statusLine(region).textContent).toBe(''), GRAPH_WAIT)
    await waitFor(() => expect(again.isConnected).toBe(false), GRAPH_WAIT)
    await waitFor(() => expect(document.activeElement).toBe(heading), GRAPH_WAIT)
    expect(first.calls()).toBe(5)
    expect(first.sent()).toEqual([entityRequest(7), entityRequest(7), entityRequest(7), entityRequest(7), entityRequest(7), entityRequest(7)])
    cleanup()
    server.resetHandlers()

    // A 404 offers none; it marked the page stale and graphMayResend sends a not_found again, so a reconnect re-reads it at once.
    const second = await reopenedOver([() => HttpResponse.json({ detail: 'Not found.' }, { status: 404 }), loaded])
    region = second.region
    await waitFor(() => expect(statusLine(region).textContent).toBe(NOT_AVAILABLE), GRAPH_WAIT)
    expect(within(region).getByText(PROVISIONAL)).toBeInTheDocument()
    expect(within(region).queryByRole('button', { name: TRY_AGAIN })).toBeNull()
    await reconnect()
    await waitFor(() => expect(statusLine(region).textContent).toBe(''), GRAPH_WAIT)
    expect(second.calls()).toBe(2)
    expect(within(region).getByText(PROVISIONAL)).toBeInTheDocument()
    expect(second.sent()).toEqual([entityRequest(7), entityRequest(7), entityRequest(7)])
    cleanup()
    server.resetHandlers()

    // A 200 the guard refuses gives the bad-body words; a refused re-read over it keeps them, that row coming first.
    const third = await reopenedOver([
      () => HttpResponse.json({ api_version: 'graph/v1' }),
      () => HttpResponse.json({ detail: 'Request was throttled.' }, { status: 429 }),
    ])
    region = third.region
    await third.settled(1)
    expect(statusLine(region).textContent).toBe(BAD_BODY)
    expect(within(region).queryByText(PROVISIONAL)).toBeNull()
    expect(within(region).queryByRole('button', { name: TRY_AGAIN })).toBeNull()
    now.mockImplementation(() => realNow() + 122_000)
    await reconnect()
    await third.settled(2)
    expect(statusLine(region).textContent).toBe(BAD_BODY)
    expect(within(region).queryByRole('button', { name: TRY_AGAIN })).toBeNull()
    now.mockRestore()
    expect(third.sent()).toEqual([entityRequest(7), entityRequest(7), entityRequest(7)])
  }, 20_000)
})
