import { QueryObserver, onlineManager } from '@tanstack/react-query'
import { act, cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { StrictMode, useState } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  levelAssignedPage, makeEvidenceResponse, makeNetworkResponse, makeRelationshipResponse, presentationAllKeys, twinEdges, unspecifiedPage,
} from '../../../mocks/fixtures/typedGraph/extras'
import { typedGraphVisitorHandlers } from '../../../mocks/handlers/typedGraph'
import { server } from '../../../mocks/server'
import { useAuthStore } from '../../../store/authStore'
import { GRAPH_WAIT, graphWrapper, newGraphClient, recorded, recordRequests } from '../../../test/graphHooks'
import type { RequestLog } from '../../../test/graphHooks'
import type { GraphEdge, GraphNetworkResponse, GraphRelationshipResponse, GraphVocabularies } from '../types'
import { graphOfNetwork } from '../network/graph'
import type { TypedGraph } from '../network/graph'
import { edgeDescription } from '../network/words'
import { RelationshipPanel } from './RelationshipPanel'
import type { RelationshipPanelProps } from './RelationshipPanel'
import { TypedSelectionPanel } from './TypedSelectionPanel'
import type { PanelSelection } from './selection'

// React's one log of an error a boundary caught: 16.0's only allowance, in a row that throws on purpose.
const CAUGHT = '%o\n\n%s\n\n%s\n'

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

/** Takes the row's one caught-error log, failing unless it is the only console call so far. */
function takeCaughtLog(): void {
  expect(vi.mocked(console.warn)).not.toHaveBeenCalled()
  expect(vi.mocked(console.error).mock.calls.map((args) => args[0])).toEqual([CAUGHT])
  vi.mocked(console.error).mockClear()
}

// Words copied from the spec's section 8, never from the module under test.
const LOADING = 'Loading this relationship…'
const LOADING_EVIDENCE = 'Loading the evidence…'
const TRY_AGAIN = 'Try loading this relationship again'
const BAD_BODY = "The server's answer could not be read."
const NOT_AVAILABLE = 'This relationship is not available to you.'
const TOO_EXPENSIVE = 'This relationship took too long to compute. Narrow the filters before trying again.'
const THROTTLED = "Too many requests: relationships share the network's limit. Try again shortly."
const DIRECT = 'Strongest level stated by the supporting results in view: direct interaction'
const NOT_REQUESTED = 'not among the requested outcomes'
const NOT_LISTED = 'Under the current filters a network would not list this relationship: none of its results in view has a requested outcome.'
const SYNTHETIC = 'Synthetic demonstration data: made up to demonstrate openPIP; it describes no real biology.'
const DEFINITION = 'Independent evidence counts observations, not records. Two results describe one observation, and count once, when they have the same outcome and the same origin (the paper that reports them or, when no paper is named as reporting them, the dataset of their release) and their detection methods match or overlap, or either names none; or when they have the same outcome and the source groups them as one observation in one release. Results linked through others count once with them. A record that names two papers as reporting it gives one result per reporting paper, each with that paper as its origin; a paper it only cites, or names for a method, adds no result. A result that a later release of the same dataset restates with the same outcome, the same reporting paper (or none in either release), and matching detection methods counts once, even when the two state different effects.'
const LIMITS = [
  'Two experiments with the same outcome, reported in one paper, or stated by one dataset when no paper reports them, count once when their records share a detection method or one names none, whatever evidence basis each states.',
  'One experiment whose method two sources name at different levels of detail in PSI-MI counts twice.',
  'openPIP does not yet read the identifiers that sources share for one observation (an IMEx interaction id, for example); once it does, a record that carries one and a record of the same experiment that does not will count twice.',
]
const P1_P2 = 'Physical interaction between SYN-P1 (Protein (UniProtKB entry)) and SYN-P2 (Protein (UniProtKB entry))'

const REL = '/api/graph/v1/relationships/'
const pageRequest = (id: number, search = '') => [`${REL}relationship%3A${id}`, search]
const evidenceRequest = (id: number, search = '') => [`${REL}relationship%3A${id}/evidence`, search]
const requests = () => log.urls.map((url) => [url.pathname, url.search])

const vocabularies = () => recorded<GraphVocabularies>('vocabularies').body
const network = (name: string): TypedGraph => graphOfNetwork(recorded<GraphNetworkResponse>(name).body)

/** Inside act, so that the debounce's update and the query's land where React expects them. */
async function pause(ms: number): Promise<void> {
  await act(async () => {
    await new Promise<void>((resolve) => setTimeout(resolve, ms))
  })
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

function edgeOf(graph: TypedGraph, id: string): GraphEdge {
  const edge = graph.edges.find((candidate) => candidate.id === id)
  if (edge === undefined) throw new Error(`no ${id} in the graph`)
  return edge
}

function edgeSelection(graph: TypedGraph, id: string): PanelSelection {
  return { kind: 'edge', id, record: edgeOf(graph, id) }
}

/** Through the dispatcher, as a page opens a relationship from a recorded network. */
function openFrom(name: string, id: string, onSelect?: (selection: PanelSelection | null) => void) {
  const graph = network(name)
  const view = render(
    <TypedSelectionPanel selection={edgeSelection(graph, id)} graph={graph} vocabularies={vocabularies()} onSelect={onSelect} onClose={() => {}} />,
    { wrapper: graphWrapper(newGraphClient()) },
  )
  return { view, graph }
}

function openPanel(ref: string, params: RelationshipPanelProps['params']) {
  return render(
    <RelationshipPanel relationshipRef={ref} params={params} vocabularies={vocabularies()} onClose={() => {}} />,
    { wrapper: graphWrapper(newGraphClient()) },
  )
}

/** The body's status line is its paragraph; the evidence list's region is a div. */
function bodyLine(region: HTMLElement): HTMLElement {
  const lines = within(region).getAllByRole('status').filter((status) => status.tagName === 'P')
  expect(lines).toHaveLength(1)
  return lines[0]
}

function paragraphWith(region: HTMLElement, test: (text: string) => boolean): HTMLElement {
  const found = Array.from(region.querySelectorAll('p')).filter((paragraph) => test(paragraph.textContent ?? ''))
  expect(found).toHaveLength(1)
  return found[0]
}

function contradictionNote(region: HTMLElement): string {
  return paragraphWith(region, (text) => text.startsWith('Contradicting results in view:')).textContent ?? ''
}

function rowOf(region: HTMLElement, id: string): HTMLElement {
  const items = within(region).getAllByRole('listitem').filter((item) => (item.textContent ?? '').includes(`(${id})`))
  expect(items).toHaveLength(1)
  return items[0]
}

function fieldLines(row: HTMLElement, term: string): string[] {
  const terms = Array.from(row.querySelectorAll('dt')).filter((dt) => dt.textContent === term)
  expect(terms, term).toHaveLength(1)
  const detail = terms[0].nextElementSibling
  if (detail === null || detail.tagName !== 'DD') throw new Error(`no detail after ${term}`)
  return Array.from(detail.children).map((line) => line.textContent ?? '')
}

/** A release entry of the release list: only those name a status. */
function releaseEntry(region: HTMLElement, release: string): HTMLElement {
  const entries = Array.from(region.querySelectorAll('dl')).filter((list) => {
    const terms = Array.from(list.querySelectorAll('dt')).map((dt) => dt.textContent)
    return terms.includes('Status') && list.querySelector('dd')?.textContent === release
  })
  expect(entries, release).toHaveLength(1)
  return entries[0]
}

describe('RelationshipPanel', () => {
  it('RP-1: relationship:3 from network-p1-reference: its level, its two counts and the counting limits', async () => {
    openFrom('network-p1-reference', 'relationship:3')
    const region = screen.getByRole('region', { name: P1_P2 })
    const page = recorded<GraphRelationshipResponse>('relationship-physical').body
    await within(region).findByText('2 of 2 results shown.', {}, GRAPH_WAIT)
    await waitFor(() => expect(within(region).getAllByRole('status')[0].textContent).toBe(''), GRAPH_WAIT)
    expect(paragraphWith(region, (text) => text === `Physical interaction: ${page.definition}`)).toBeInTheDocument()
    expect(within(region).getByText('Strongest level stated by the supporting results in view: direct interaction')).toBeInTheDocument()
    expect(within(region).getByText('Supporting results by level: 1 at direct interaction; 1 at physical association')).toBeInTheDocument()
    const headline = paragraphWith(region, (text) => text.startsWith('Supporting results (assertions): '))
    expect(headline.textContent).toBe('Supporting results (assertions): 2Independent evidence: 1')
    expect(headline.querySelector('br')).not.toBeNull()
    expect(within(region).getByRole('row', { name: 'supporting 2 1' })).toBeInTheDocument()
    expect(within(region).getByText('Publications reporting the supporting results: none named as reporting them')).toBeInTheDocument()
    expect(within(region).getByText(DEFINITION)).toBeInTheDocument()
    expect(within(region).getByText('The count has these stated limits:')).toBeInTheDocument()
    expect(within(region).getAllByRole('list').some((list) => Array.from(list.children).map((item) => item.textContent).join('\n') === LIMITS.join('\n'))).toBe(true)
    expect(requests()).toEqual([pageRequest(3), evidenceRequest(3)])
  }, 20_000)

  it('RP-2: relationship:4 from network-all-edges: the level not stated, the tested level, the contradiction, its ends', async () => {
    openFrom('network-all-edges', 'relationship:4')
    const region = screen.getByRole('region')
    await within(region).findByText('2 of 2 results shown.', {}, GRAPH_WAIT)
    expect(within(region).getByText('Strongest level stated by the supporting results in view: physical interaction, level not stated')).toBeInTheDocument()
    expect(within(region).getByText('Tested for direct interaction: tested, not detected')).toBeInTheDocument()
    expect(contradictionNote(region)).toBe('Contradicting results in view: 1 supporting result and 1 result tested for direct interaction and not detected.')
    expect(within(region).getByRole('button', { name: 'SYN-P2 (Protein (UniProtKB entry))' })).toBeInTheDocument()
    expect(within(region).getByRole('button', { name: 'SYN-SHARED (Protein (UniProtKB entry))' })).toBeInTheDocument()
    expect(requests()).toEqual([pageRequest(4, '?outcome=all'), evidenceRequest(4)])
  }, 20_000)

  it('RP-3: an assigned level and the unspecified type over stubbed pages with no edge; no level line for a regulation', async () => {
    const stubbed: [number, GraphRelationshipResponse, string][] = [
      [909, levelAssignedPage, 'Strongest level of the supporting results in view: co-complex association, assigned by openPIP from the stated assay'],
      [910, unspecifiedPage, 'Strongest level stated by the supporting results in view: none: this type states no interaction type or level and claims no physical contact'],
    ]
    for (const [id, body, words] of stubbed) {
      server.use(http.get(`${REL}:ref`, () => HttpResponse.json(body)))
      const view = openPanel(`relationship:${id}`, {})
      const region = screen.getByRole('region', { name: `Relationship relationship:${id}` })
      await within(region).findByText(words, {}, GRAPH_WAIT)
      await within(region).findByText('The evidence could not be loaded (HTTP 501).', {}, GRAPH_WAIT)
      if (id === 910) expect(region.textContent).not.toMatch(/physical interaction/i)
      expect(requests().slice(-2)).toEqual([pageRequest(id), evidenceRequest(id)])
      view.unmount()
      server.resetHandlers()
    }
    openPanel('relationship:9', {})
    const region = screen.getByRole('region')
    await within(region).findByText('2 of 2 results shown.', {}, GRAPH_WAIT)
    expect(within(region).getByText('Counts in view')).toBeInTheDocument()
    expect(within(region).queryByText(/^Strongest level/)).toBeNull()
    expect(requests()).toEqual([pageRequest(909), evidenceRequest(909), pageRequest(910), evidenceRequest(910), pageRequest(9), evidenceRequest(9)])
  }, 20_000)

  it("RP-4: relationship:4 from network-p2-tested-absent: the 501, the kept edge's words and no 'no support'", async () => {
    const { graph } = openFrom('network-p2-tested-absent', 'relationship:4')
    const region = screen.getByRole('region')
    await within(region).findByText('This relationship could not be loaded (HTTP 501).', {}, GRAPH_WAIT)
    await within(region).findByText('2 of 2 results shown.', {}, GRAPH_WAIT)
    expect(region.textContent).not.toContain('No recorded typed-graph response')
    const headline = paragraphWith(region, (text) => text.startsWith('Supporting results (assertions): '))
    expect(headline.textContent).toBe(`Supporting results (assertions): ${NOT_REQUESTED}Independent evidence: ${NOT_REQUESTED}`)
    expect(within(region).getByText('Strongest level stated by the supporting results in view: not shown, since supporting results are not among the requested outcomes')).toBeInTheDocument()
    const sentence = edgeDescription(edgeOf(graph, 'relationship:4'), graph, null, vocabularies())
    const sentenceLine = paragraphWith(region, (text) => text === sentence)
    // The network's sentence still says it, and is only ever compared whole; no line the panel adds says it.
    const lines = Array.from(region.querySelectorAll('p, li, th, td, dd span'))
      .filter((element) => element !== sentenceLine)
      .map((element) => element.textContent ?? '')
    expect(lines.filter((text) => /no support/i.test(text))).toEqual([])
    const supported = rowOf(region, 'evidence:35')
    expect(within(supported).getByText('Supporting result (not among the requested outcomes)')).toBeInTheDocument()
    expect(fieldLines(supported, 'Effect')).toEqual(['not applicable: this type states no effect'])
    expect(Array.from(region.querySelectorAll('dt')).filter((dt) => dt.textContent === 'Genetic sign')).toEqual([])
    expect(fieldLines(supported, 'Participants')[0]).toMatch(/^SYN-P3 \(participant, as written\)/)
    expect(requests()).toEqual([pageRequest(4, '?outcome=tested_absent'), evidenceRequest(4)])
  }, 20_000)

  it('RP-5: relationship:3 under level=physical_association: the results outside the filters', async () => {
    render(<RelationshipPanel relationshipRef="relationship:3" params={{ level: ['physical_association'] }} vocabularies={vocabularies()} onClose={() => {}} />,
      { wrapper: graphWrapper(newGraphClient()) })
    const region = screen.getByRole('region')
    await within(region).findByText('Results outside the current filters: 1, by outcome: 1 supporting', {}, GRAPH_WAIT)
    expect(within(region).getByText('Strongest level stated by the supporting results in view: physical association')).toBeInTheDocument()
    await within(region).findByText('1 of 1 result shown.', {}, GRAPH_WAIT)
    expect(requests()).toEqual([pageRequest(3, '?level=physical_association'), evidenceRequest(3, '?level=physical_association')])
  }, 20_000)

  it('RP-6: relationship:6 from network-tf1-both-releases: a mixed effect across a corrected and a current release', async () => {
    openFrom('network-tf1-both-releases', 'relationship:6')
    const region = screen.getByRole('region')
    await within(region).findByText('2 of 2 results shown.', {}, GRAPH_WAIT)
    expect(within(region).getByText('Supporting results in view: 1 states activation, 1 states inhibition')).toBeInTheDocument()
    expect(contradictionNote(region)).toBe('Contradicting results in view: Supporting results state different effects: 1 states activation, 1 states inhibition. The supporting results come from more than one release. Synthetic regulation example v1 is among them, and a newer release has since corrected it.')
    const status = (release: string) => Array.from(releaseEntry(region, release).querySelectorAll('dt'))
      .find((dt) => dt.textContent === 'Status')?.nextElementSibling?.textContent
    expect(status('Synthetic regulation example v1')).toBe('superseded: a newer release has since corrected it')
    expect(status('Synthetic regulation example v2')).toBe('published')
    expect(requests()).toEqual([pageRequest(6, '?releases=release:1,release:2'), evidenceRequest(6, '?releases=release:1,release:2')])
  }, 20_000)

  it('RP-7: relationship:7 with no query: shown false, so the network would not list it; the synthetic words', async () => {
    openPanel('relationship:7', {})
    const region = screen.getByRole('region')
    await within(region).findByText(NOT_LISTED, {}, GRAPH_WAIT)
    const sentence = paragraphWith(region, (text) => text.startsWith('Transcriptional regulation: SYN-TF1'))
    expect(sentence.textContent).toContain('SYN-TF1 (regulator) to SYN-G2 (target)')
    expect(sentence.textContent).toContain('no supporting evidence in view')
    expect(within(region).getByText(SYNTHETIC)).toBeInTheDocument()
    await within(region).findByText('1 of 1 result shown.', {}, GRAPH_WAIT)
    expect(requests()).toEqual([pageRequest(7), evidenceRequest(7)])
  }, 20_000)

  it('RP-8: nothing is sent before the debounce, then the two requests (real timers)', async () => {
    openFrom('network-p1-reference', 'relationship:3')
    await pause(200)
    const region = screen.getByRole('region', { name: P1_P2 })
    expect(within(region).getByRole('heading', { level: 2, name: P1_P2 })).toBeInTheDocument()
    expect(within(region).getByRole('button', { name: 'Close' })).toBeInTheDocument()
    expect(within(region).getByText(LOADING)).toBeInTheDocument()
    expect(log.urls).toEqual([])
    // The waiting view states synthetic data from the kept edge, before any page answers.
    expect(within(region).getByText(SYNTHETIC)).toBeInTheDocument()
    await pause(300)
    expect(requests()).toEqual([pageRequest(3), evidenceRequest(3)])
    await within(region).findByText('2 of 2 results shown.', {}, GRAPH_WAIT)
  }, 20_000)

  it('RP-8: nothing is sent before the debounce, then the two requests (fake timers that advance)', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    try {
      openFrom('network-p1-reference', 'relationship:3')
      await act(async () => {
        await vi.advanceTimersByTimeAsync(250)
      })
      expect(log.urls).toEqual([])
      await act(async () => {
        await vi.advanceTimersByTimeAsync(100)
      })
      expect(requests()).toEqual([pageRequest(3), evidenceRequest(3)])
      await within(screen.getByRole('region')).findByText('2 of 2 results shown.', {}, GRAPH_WAIT)
    } finally {
      cleanup()
      vi.useRealTimers()
    }
  }, 20_000)

  it('RP-9: a stored 503 offers Try again; a reopening does not send it again; the retry keeps its button and focus', async () => {
    const user = userEvent.setup()
    const hold = holdRequests()
    let calls = 0
    server.use(http.get(`${REL}:ref`, async () => {
      calls += 1
      if (calls === 2) await hold.wait()
      return HttpResponse.json({ detail: 'Too expensive.' }, { status: 503 })
    }))
    const graph = network('network-all-edges')
    const Wrapper = graphWrapper(newGraphClient())
    const panel = (open: boolean) => (
      <Wrapper>
        {open && <TypedSelectionPanel selection={edgeSelection(graph, 'relationship:9')} graph={graph} vocabularies={vocabularies()} onClose={() => {}} />}
      </Wrapper>
    )
    const view = render(panel(true))
    let region = screen.getByRole('region')
    await within(region).findByText(TOO_EXPENSIVE, {}, GRAPH_WAIT)
    const line = within(region).getAllByRole('status').find((status) => status.textContent === TOO_EXPENSIVE)
    if (line === undefined) throw new Error('the words are in no status region')
    const offered = within(region).getByRole('button', { name: TRY_AGAIN })
    expect(line.compareDocumentPosition(offered) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(calls).toBe(1)
    view.rerender(panel(false))
    expect(screen.queryByRole('region')).toBeNull()
    view.rerender(panel(true))
    await pause(600)
    expect(calls).toBe(1)
    region = screen.getByRole('region')
    await within(region).findByText(TOO_EXPENSIVE, {}, GRAPH_WAIT)
    const button = within(region).getByRole('button', { name: TRY_AGAIN })
    await user.click(button)
    // The retry is held: the same button stays, aria-disabled, with focus, beside the loading words.
    await waitFor(() => expect(hold.waiting()).toBe(1), GRAPH_WAIT)
    expect(calls).toBe(2)
    await waitFor(() => expect(button.getAttribute('aria-disabled')).toBe('true'), GRAPH_WAIT)
    expect(within(region).getByRole('button', { name: TRY_AGAIN })).toBe(button)
    expect(button.hasAttribute('disabled')).toBe(false)
    expect(document.activeElement).toBe(button)
    expect(within(region).getAllByRole('status').some((status) => status.textContent === LOADING)).toBe(true)
    hold.release()
    await waitFor(() => expect(button.getAttribute('aria-disabled')).toBeNull(), GRAPH_WAIT)
    await within(region).findByText(TOO_EXPENSIVE, {}, GRAPH_WAIT)
    expect(button.isConnected).toBe(true)
    expect(document.activeElement).toBe(button)
    expect(calls).toBe(2)
    expect(requests()).toEqual([pageRequest(9, '?outcome=all'), evidenceRequest(9), pageRequest(9, '?outcome=all')])
  }, 20_000)

  it('RP-10: a stored 503 is not sent again through relationship:9, relationship:3, relationship:9', async () => {
    let nine = 0
    server.use(http.get(`${REL}:ref`, ({ params }) => {
      if (decodeURIComponent(String(params.ref)) !== 'relationship:9') return undefined
      nine += 1
      return HttpResponse.json({ detail: 'Too expensive.' }, { status: 503 })
    }))
    const graph = network('network-all-edges')
    const Wrapper = graphWrapper(newGraphClient())
    const panel = (id: string) => (
      <Wrapper>
        <TypedSelectionPanel selection={edgeSelection(graph, id)} graph={graph} vocabularies={vocabularies()} onClose={() => {}} />
      </Wrapper>
    )
    const view = render(panel('relationship:9'))
    await within(screen.getByRole('region')).findByText(TOO_EXPENSIVE, {}, GRAPH_WAIT)
    view.rerender(panel('relationship:3'))
    await within(screen.getByRole('region')).findByText('2 of 2 results shown.', {}, GRAPH_WAIT)
    view.rerender(panel('relationship:9'))
    await pause(600)
    expect(within(screen.getByRole('region')).getByText(TOO_EXPENSIVE)).toBeInTheDocument()
    expect(nine).toBe(1)
    expect(requests()).toEqual([pageRequest(9, '?outcome=all'), evidenceRequest(9), pageRequest(3, '?outcome=all'), evidenceRequest(3)])
  }, 20_000)

  it("RP-11: a visitor's 404 never claims the relationship does not exist, and a reopening sends it again", async () => {
    server.use(...typedGraphVisitorHandlers)
    const Wrapper = graphWrapper(newGraphClient())
    const edge = recorded<GraphRelationshipResponse>('relationship-mixed').body.edge
    const panel = (open: boolean) => (
      <Wrapper>
        {open && <RelationshipPanel relationshipRef="relationship:9" params={{}} edge={edge} vocabularies={vocabularies()} onClose={() => {}} />}
      </Wrapper>
    )
    const view = render(panel(true))
    const region = screen.getByRole('region')
    await within(region).findByText(NOT_AVAILABLE, {}, GRAPH_WAIT)
    for (const banned of [/not enabled/i, /does not exist/i, /Not found\./]) expect(region.textContent).not.toMatch(banned)
    expect(requests()).toEqual([pageRequest(9), evidenceRequest(9)])
    view.rerender(panel(false))
    view.rerender(panel(true))
    await pause(600)
    await within(screen.getByRole('region')).findByText(NOT_AVAILABLE, {}, GRAPH_WAIT)
    expect(requests().filter(([path]) => path === `${REL}relationship%3A9`)).toEqual([pageRequest(9), pageRequest(9)])
    // The evidence's 501 is an `http` failure, which the hook also sends again on mount.
    expect(requests()).toEqual([pageRequest(9), evidenceRequest(9), pageRequest(9), evidenceRequest(9)])
  }, 20_000)

  it('RP-12: a body the guard refuses, then one that throws, and the boundary that resets with the body', async () => {
    const mixed = recorded<GraphRelationshipResponse>('relationship-mixed').body
    const refused: unknown[] = [{ api_version: 'graph/v1' }, { ...mixed, edge: { ...mixed.edge, id: 'relationship:999' } }]
    // Each body under a render of its own and a fresh client: a 200 stays fresh, and a bad body offers no retry.
    for (const body of refused) {
      server.use(http.get(`${REL}:ref`, () => HttpResponse.json(body as Record<string, unknown>)))
      const view = openPanel('relationship:9', {})
      const region = screen.getByRole('region')
      await waitFor(() => expect(within(region).getAllByRole('status')[0].textContent).toBe(BAD_BODY), GRAPH_WAIT)
      view.unmount()
      server.resetHandlers()
    }
    server.use(http.get(`${REL}:ref`, () => HttpResponse.json({ edge: { id: 'relationship:9' } })))
    const Wrapper = graphWrapper(newGraphClient())
    const panel = (params: RelationshipPanelProps['params']) => (
      <Wrapper>
        <RelationshipPanel relationshipRef="relationship:9" params={params} vocabularies={vocabularies()} onClose={() => {}} />
        <p>The rest of the page</p>
      </Wrapper>
    )
    const view = render(panel({}))
    const region = screen.getByRole('region', { name: 'Relationship relationship:9' })
    const caught = await within(region).findByText(BAD_BODY, {}, GRAPH_WAIT)
    // From PanelBoundary: its own paragraph in place of the body, with the body's status regions gone.
    expect(caught.closest('[role="status"]')).toBeNull()
    expect(within(region).queryAllByRole('status')).toEqual([])
    expect(within(region).getByRole('heading', { level: 2, name: 'Relationship relationship:9' })).toBeInTheDocument()
    expect(within(region).getByRole('button', { name: 'Close' })).toBeInTheDocument()
    expect(screen.getByText('The rest of the page')).toBeInTheDocument()
    takeCaughtLog()
    server.resetHandlers()
    view.rerender(panel({ outcome: 'all' }))
    await within(region).findByText('Counts in view', {}, GRAPH_WAIT)
    expect(within(region).queryByText(BAD_BODY)).toBeNull()
    expect(screen.getByRole('region', { name: 'Relationship relationship:9' })).toBe(region)
    expect(requests().filter(([path]) => path === `${REL}relationship%3A9`).map(([, search]) => search)).toEqual(['', '', '', '?outcome=all'])
    expect(requests()).toEqual([
      pageRequest(9), evidenceRequest(9), pageRequest(9), evidenceRequest(9), pageRequest(9), evidenceRequest(9), pageRequest(9, '?outcome=all'),
    ])
  }, 20_000)

  it('RP-13: a 429 states its Retry-After, offers Try again, and a reopening sends the page again', async () => {
    let pages = 0
    server.use(http.get(`${REL}:ref`, () => {
      pages += 1
      return HttpResponse.json({ detail: 'Request was throttled.' }, { status: 429, headers: { 'Retry-After': '30' } })
    }))
    const graph = network('network-all-edges')
    const Wrapper = graphWrapper(newGraphClient())
    const panel = (open: boolean) => (
      <Wrapper>
        {open && <TypedSelectionPanel selection={edgeSelection(graph, 'relationship:9')} graph={graph} vocabularies={vocabularies()} onClose={() => {}} />}
      </Wrapper>
    )
    const view = render(panel(true))
    const region = screen.getByRole('region')
    await within(region).findByText("Too many requests: relationships share the network's limit. Try again in 30 seconds.", {}, GRAPH_WAIT)
    expect(within(region).getByRole('button', { name: TRY_AGAIN })).toBeInTheDocument()
    view.rerender(panel(false))
    view.rerender(panel(true))
    await pause(600)
    expect(pages).toBe(2)
    expect(requests()).toEqual([pageRequest(9, '?outcome=all'), evidenceRequest(9), pageRequest(9, '?outcome=all')])
  }, 20_000)

  it('RP-14: focus on the heading, Escape and Close, no Close without onClose, and the heading levels', async () => {
    const user = userEvent.setup()
    const onClose = vi.fn()
    const graph = network('network-p1-reference')
    const reachedPage = vi.fn()
    const listen = (event: KeyboardEvent) => {
      if (event.key === 'Escape') reachedPage()
    }
    document.addEventListener('keydown', listen)
    try {
      const view = render(
        <TypedSelectionPanel selection={edgeSelection(graph, 'relationship:3')} graph={graph} vocabularies={vocabularies()} onClose={onClose} headingLevel={3} />,
        { wrapper: graphWrapper(newGraphClient()) },
      )
      const region = screen.getByRole('region', { name: P1_P2 })
      const heading = within(region).getByRole('heading', { level: 3, name: P1_P2 })
      await waitFor(() => expect(document.activeElement).toBe(heading))
      expect(heading.getAttribute('tabindex')).toBe('-1')
      await user.keyboard('{Escape}')
      expect(onClose).toHaveBeenCalledTimes(1)
      expect(reachedPage).not.toHaveBeenCalled()
      await user.click(within(region).getByRole('button', { name: 'Close' }))
      expect(onClose).toHaveBeenCalledTimes(2)
      await within(region).findByText('2 of 2 results shown.', {}, GRAPH_WAIT)
      const subHeadings = within(region).getAllByRole('heading', { level: 4 })
      expect(subHeadings.map((element) => element.textContent)).toEqual(expect.arrayContaining(['Summary', 'Counts in view', 'Evidence (every outcome)', 'Releases']))
      expect(within(region).queryAllByRole('heading', { level: 2 })).toEqual([])
      view.unmount()
      render(<TypedSelectionPanel selection={edgeSelection(graph, 'relationship:3')} graph={graph} vocabularies={vocabularies()} />,
        { wrapper: graphWrapper(newGraphClient()) })
      const second = screen.getByRole('region', { name: P1_P2 })
      await waitFor(() => expect(document.activeElement).toBe(within(second).getByRole('heading', { level: 2, name: P1_P2 })))
      expect(within(second).queryByRole('button', { name: 'Close' })).toBeNull()
      // With nothing to close, Escape is left to the page.
      await user.keyboard('{Escape}')
      expect(reachedPage).toHaveBeenCalledTimes(1)
      expect(onClose).toHaveBeenCalledTimes(2)
      expect(second.isConnected).toBe(true)
      await within(second).findByText('2 of 2 results shown.', {}, GRAPH_WAIT)
    } finally {
      document.removeEventListener('keydown', listen)
    }
    expect(requests()).toEqual([pageRequest(3), evidenceRequest(3), pageRequest(3), evidenceRequest(3)])
  }, 20_000)

  it("RP-15: two panels on one page keep distinct names and ids, no two buttons of a panel share a name, and twins' panels are named apart", async () => {
    const graph = network('network-all-edges')
    const view = render(
      <>
        <TypedSelectionPanel selection={edgeSelection(graph, 'relationship:3')} graph={graph} vocabularies={vocabularies()} onClose={() => {}} />
        <TypedSelectionPanel selection={edgeSelection(graph, 'relationship:9')} graph={graph} vocabularies={vocabularies()} onClose={() => {}} />
      </>,
      { wrapper: graphWrapper(newGraphClient()) },
    )
    const regions = screen.getAllByRole('region')
    expect(regions).toHaveLength(2)
    for (const region of regions) await within(region).findByText(/^\d+ of \d+ results? shown\.$/, {}, GRAPH_WAIT)
    const names = regions.map((region) => within(region).getByRole('heading', { level: 2 }).textContent)
    expect(new Set(names).size).toBe(2)
    expect(regions.map((region) => region.getAttribute('aria-labelledby'))).toEqual(regions.map((region) => within(region).getByRole('heading', { level: 2 }).id))
    expect(new Set(regions.map((region) => region.getAttribute('aria-labelledby'))).size).toBe(2)
    for (const region of regions) {
      const buttons = within(region).getAllByRole('button').map((button) => button.getAttribute('aria-label') ?? button.textContent)
      expect(buttons.filter((name, index) => buttons.indexOf(name) !== index)).toEqual([])
    }
    const sorted = (list: string[][]) => [...list].sort((a, b) => a.join(' ').localeCompare(b.join(' ')))
    expect(sorted(requests())).toEqual(sorted([pageRequest(3, '?outcome=all'), evidenceRequest(3), pageRequest(9, '?outcome=all'), evidenceRequest(9)]))
    view.unmount()

    // Two relationships of one type and ends: each panel takes its ref, as its row does, before and after its page loads.
    server.use(
      http.get(`${REL}:ref`, ({ params }) => HttpResponse.json(makeRelationshipResponse({
        edge: twinEdges[decodeURIComponent(String(params.ref)) === 'relationship:931' ? 0 : 1],
      }))),
      http.get(`${REL}:ref/evidence`, () => HttpResponse.json(makeEvidenceResponse())),
    )
    const twins = graphOfNetwork(makeNetworkResponse({ edges: twinEdges }))
    render(
      <>
        <TypedSelectionPanel selection={edgeSelection(twins, 'relationship:931')} graph={twins} vocabularies={vocabularies()} onClose={() => {}} />
        <TypedSelectionPanel selection={edgeSelection(twins, 'relationship:932')} graph={twins} vocabularies={vocabularies()} onClose={() => {}} />
      </>,
      { wrapper: graphWrapper(newGraphClient()) },
    )
    const twinNames = [931, 932].map((id) => `Transcriptional regulation: SYN-N901 (Gene) to SYN-N902 (Gene) (relationship:${id})`)
    const headings = () => screen.getAllByRole('region').map((region) => within(region).getByRole('heading', { level: 2 }).textContent)
    expect(headings()).toEqual(twinNames)
    for (const region of screen.getAllByRole('region')) {
      await waitFor(() => expect(within(region).getAllByRole('status')[0].textContent).toBe(''), GRAPH_WAIT)
    }
    expect(headings()).toEqual(twinNames)
    expect(sorted(requests().slice(4))).toEqual(sorted([pageRequest(931), evidenceRequest(931), pageRequest(932), evidenceRequest(932)]))
  }, 20_000)

  it("RP-16: an end button selects its entity with the page's node and the panel's params", async () => {
    const user = userEvent.setup()
    const onSelect = vi.fn()
    openFrom('network-p1-reference', 'relationship:3', onSelect)
    const region = screen.getByRole('region')
    await within(region).findByText('2 of 2 results shown.', {}, GRAPH_WAIT)
    await waitFor(() => expect(within(region).getAllByRole('status')[0].textContent).toBe(''), GRAPH_WAIT)
    await user.click(within(region).getByRole('button', { name: 'SYN-P1 (Protein (UniProtKB entry))' }))
    const page = recorded<GraphRelationshipResponse>('relationship-physical').body
    expect(onSelect).toHaveBeenCalledTimes(1)
    expect(onSelect).toHaveBeenCalledWith({ kind: 'node', id: 'entity:10', record: page.nodes.find((node) => node.id === 'entity:10'), params: {} })
    expect(requests()).toEqual([pageRequest(3), evidenceRequest(3)])
  }, 20_000)

  it('RP-17: node_label_namespace names SYN-G3 in the heading and the sentence, never SYN-ALPHA', async () => {
    const graph = network('network-all-edges')
    render(
      <TypedSelectionPanel selection={edgeSelection(graph, 'relationship:11')} graph={graph} vocabularies={vocabularies()} presentation={presentationAllKeys} onClose={() => {}} />,
      { wrapper: graphWrapper(newGraphClient()) },
    )
    const region = screen.getByRole('region')
    await within(region).findByText('This relationship could not be loaded (HTTP 501).', {}, GRAPH_WAIT)
    expect(within(region).getByRole('heading', { level: 2 }).textContent).toContain('SYN-G3')
    const sentence = edgeDescription(edgeOf(graph, 'relationship:11'), graph, presentationAllKeys, vocabularies())
    expect(sentence).toContain('SYN-G3')
    expect(paragraphWith(region, (text) => text === sentence)).toBeInTheDocument()
    expect(region.textContent).not.toContain('SYN-ALPHA')
    expect(requests()).toEqual([pageRequest(11, '?outcome=all'), evidenceRequest(11)])
  }, 20_000)

  it('RP-18: inline styles only, http(s) links, lists that keep their role, and the boundary that carries the source text style', async () => {
    const opened: [string, string][] = [['network-p1-reference', 'relationship:3'], ['network-tf1-both-releases', 'relationship:6']]
    for (const [name, id] of opened) {
      const { view } = openFrom(name, id)
      const section = screen.getByRole('region')
      await within(section).findByText('2 of 2 results shown.', {}, GRAPH_WAIT)
      await waitFor(() => expect(within(section).getAllByRole('status')[0].textContent).toBe(''), GRAPH_WAIT)
      const { container } = view
      expect(container.querySelector('[class]')).toBeNull()
      const links = Array.from(container.querySelectorAll('a[href]'))
      expect(links.length).toBeGreaterThan(0)
      for (const link of links) {
        expect(link.getAttribute('href')).toMatch(/^https?:\/\//)
        expect(link.getAttribute('target')).toBe('_blank')
        expect(link.getAttribute('rel')).toBe('noopener noreferrer')
      }
      expect(container.querySelectorAll('ul:not([role="list"])')).toHaveLength(0)
      for (const button of Array.from(container.querySelectorAll('button'))) {
        expect(button.style.cursor, button.textContent ?? '').toBe(button.getAttribute('aria-disabled') === 'true' ? 'default' : 'pointer')
      }
      for (const heading of Array.from(container.querySelectorAll<HTMLElement>('h1, h2, h3, h4, h5, h6'))) {
        expect(heading.style.fontWeight, heading.textContent ?? '').toBe('600')
      }
      expect(section.hasAttribute('data-selectable')).toBe(true)
      const boundaries = Array.from(section.children).filter((child) => child.tagName === 'DIV')
      expect(boundaries).toHaveLength(1)
      const boundary = boundaries[0] as HTMLElement
      expect(boundary.style.whiteSpace).toBe('pre-wrap')
      expect(boundary.style.overflowWrap).toBe('anywhere')
      const heading = within(section).getByRole('heading', { level: 2 })
      const close = within(section).getByRole('button', { name: 'Close' })
      const holders = [section, ...Array.from(section.querySelectorAll('*'))]
        .filter((element) => !boundary.contains(element))
        .filter((element) => Array.from(element.childNodes).some((node) => node.nodeType === Node.TEXT_NODE && (node.textContent ?? '').trim() !== ''))
      expect(holders).toHaveLength(2)
      expect(holders[0]).toBe(heading)
      expect(holders[1]).toBe(close)
      view.unmount()
    }
    expect(requests()).toEqual([
      pageRequest(3), evidenceRequest(3),
      pageRequest(6, '?releases=release:1,release:2'), evidenceRequest(6, '?releases=release:1,release:2'),
    ])
  }, 20_000)

  it('RP-19: no edge and no graph, a 404: no summary while it waits, the words after, and nothing throws', async () => {
    server.use(http.get(`${REL}:ref`, () => HttpResponse.json({ detail: 'Not found.' }, { status: 404 })))
    render(<RelationshipPanel relationshipRef="relationship:999" params={{}} vocabularies={vocabularies()} onClose={() => {}} />,
      { wrapper: graphWrapper(newGraphClient()) })
    const region = screen.getByRole('region', { name: 'Relationship relationship:999' })
    expect(within(region).getByText(LOADING)).toBeInTheDocument()
    expect(within(region).queryByText('Summary')).toBeNull()
    await within(region).findByText(NOT_AVAILABLE, {}, GRAPH_WAIT)
    expect(within(region).queryByText('Summary')).toBeNull()
    await within(region).findByText(/^The evidence could not be loaded/, {}, GRAPH_WAIT)
    expect(requests()).toEqual([pageRequest(999), evidenceRequest(999)])
  }, 20_000)

  it("RP-20: relationship:6 from network-tf1: the default view's release alone, v2's effect, no contradiction", async () => {
    openFrom('network-tf1', 'relationship:6')
    const region = screen.getByRole('region')
    await within(region).findByText('1 of 1 result shown.', {}, GRAPH_WAIT)
    expect(within(region).getByText('Effect: inhibits')).toBeInTheDocument()
    expect(within(region).queryByText(/Contradicting results in view/)).toBeNull()
    expect(fieldLines(rowOf(region, 'evidence:19'), 'Release')).toEqual(['Synthetic regulation example v2 (synthetic)'])
    expect(requests()).toEqual([pageRequest(6), evidenceRequest(6)])
  }, 20_000)

  it("RP-21: RP-14's flow inside StrictMode: focus on the heading, two requests, Escape back to the opener", async () => {
    const user = userEvent.setup()
    const graph = network('network-p1-reference')
    function Harness() {
      const [open, setOpen] = useState(false)
      return (
        <>
          <button type="button" onClick={() => setOpen(true)}>Open relationship</button>
          {open && <TypedSelectionPanel selection={edgeSelection(graph, 'relationship:3')} graph={graph} vocabularies={vocabularies()} onClose={() => setOpen(false)} />}
        </>
      )
    }
    render(<StrictMode><Harness /></StrictMode>, { wrapper: graphWrapper(newGraphClient()) })
    const opener = screen.getByRole('button', { name: 'Open relationship' })
    await user.click(opener)
    const region = screen.getByRole('region', { name: P1_P2 })
    const heading = within(region).getByRole('heading', { level: 2, name: P1_P2 })
    await waitFor(() => expect(document.activeElement).toBe(heading))
    await within(region).findByText('2 of 2 results shown.', {}, GRAPH_WAIT)
    expect(requests()).toEqual([pageRequest(3), evidenceRequest(3)])
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('region')).toBeNull()
    expect(document.activeElement).toBe(opener)
    expect(requests()).toEqual([pageRequest(3), evidenceRequest(3)])
  }, 20_000)

  it('RP-22: new params move focus from the remounted body to the heading, and leave a control outside the panel alone', async () => {
    const user = userEvent.setup()
    const Wrapper = graphWrapper(newGraphClient())
    const panel = (params: RelationshipPanelProps['params']) => (
      <Wrapper>
        <button type="button">A harness button</button>
        <RelationshipPanel relationshipRef="relationship:3" params={params} vocabularies={vocabularies()} onClose={() => {}} />
      </Wrapper>
    )
    const view = render(panel({}))
    const region = screen.getByRole('region')
    const heading = within(region).getByRole('heading', { level: 2 })
    await within(region).findByText('2 of 2 results shown.', {}, GRAPH_WAIT)
    const toggle = within(region).getByRole('button', { name: "Source record's columns: syn-10, line 11 (evidence:28)" })
    toggle.focus()
    expect(document.activeElement).toBe(toggle)
    view.rerender(panel({ level: ['physical_association'] }))
    expect(toggle.isConnected).toBe(false)
    expect(document.activeElement).toBe(heading)
    await within(region).findByText('Results outside the current filters: 1, by outcome: 1 supporting', {}, GRAPH_WAIT)
    await within(region).findByText('1 of 1 result shown.', {}, GRAPH_WAIT)
    expect(document.activeElement).toBe(heading)
    expect(document.activeElement).not.toBe(document.body)
    const harness = screen.getByRole('button', { name: 'A harness button' })
    await user.click(harness)
    expect(document.activeElement).toBe(harness)
    view.rerender(panel({}))
    expect(document.activeElement).toBe(harness)
    await within(region).findByText('2 of 2 results shown.', {}, GRAPH_WAIT)
    expect(document.activeElement).toBe(harness)
    expect(requests()).toEqual([
      pageRequest(3), evidenceRequest(3),
      pageRequest(3, '?level=physical_association'), evidenceRequest(3, '?level=physical_association'),
    ])
  }, 20_000)

  it('RP-23: offline, the paused fetches read as loading, never as an error, and load once online', async () => {
    onlineManager.setOnline(false)
    openPanel('relationship:3', {})
    const region = screen.getByRole('region')
    await pause(600)
    expect(log.urls).toEqual([])
    expect(within(region).getAllByRole('status').map((status) => status.textContent)).toEqual([LOADING, LOADING_EVIDENCE])
    expect(region.textContent).not.toContain('could not be loaded')
    await act(async () => {
      onlineManager.setOnline(true)
    })
    await within(region).findByText('2 of 2 results shown.', {}, GRAPH_WAIT)
    expect(requests()).toEqual([pageRequest(3), evidenceRequest(3)])
  }, 20_000)

  it("RP-24: a reconnect's refetch replaces a focused Try again never pressed, and after a settled retry no busy Try again follows", async () => {
    const user = userEvent.setup()
    const hold = holdRequests()
    let calls = 0
    // The two reconnects' refetches, the second and the fourth page requests, are held.
    server.use(http.get(`${REL}:ref`, async () => {
      calls += 1
      const call = calls
      if (call === 2 || call === 4) await hold.wait()
      return call <= 2
        ? HttpResponse.json({ detail: 'Request was throttled.' }, { status: 429 })
        : HttpResponse.json({ detail: 'Not found.' }, { status: 404 })
    }))
    openPanel('relationship:9', {})
    const region = screen.getByRole('region')
    const heading = within(region).getByRole('heading', { level: 2 })
    const throttled = "Too many requests: relationships share the network's limit. Try again shortly."
    // The body's status line is its paragraph; the evidence list's region is a div.
    const line = () => {
      const lines = within(region).getAllByRole('status').filter((status) => status.tagName === 'P')
      expect(lines).toHaveLength(1)
      return lines[0]
    }
    const offeredNow = () => within(region).queryByRole('button', { name: TRY_AGAIN })
    const pages = () => requests().filter(([path]) => path === `${REL}relationship%3A9`).length
    await within(region).findByText(throttled, {}, GRAPH_WAIT)
    // No edge is known, since the panel has no record and its page failed, so the count names no total.
    await within(region).findByText('2 results shown.', {}, GRAPH_WAIT)
    const offered = within(region).getByRole('button', { name: TRY_AGAIN })
    offered.focus()
    expect(line().textContent).toBe(throttled)
    expect(offered.getAttribute('aria-disabled')).toBeNull()
    expect(document.activeElement).toBe(offered)
    expect(requests()).toEqual([pageRequest(9), evidenceRequest(9)])
    // Offline, then online: the reconnect's refetch. Its Try again goes and focus moves to the heading.
    act(() => {
      onlineManager.setOnline(false)
    })
    await act(async () => {
      onlineManager.setOnline(true)
    })
    await waitFor(() => expect(hold.waiting()).toBe(1), GRAPH_WAIT)
    await waitFor(() => expect(line().textContent).toBe(LOADING), GRAPH_WAIT)
    await waitFor(() => expect(document.activeElement).toBe(heading), GRAPH_WAIT)
    expect(offeredNow()).toBeNull()
    expect(pages()).toBe(2)
    hold.release()
    await waitFor(() => expect(line().textContent).toBe(throttled), GRAPH_WAIT)
    const back = within(region).getByRole('button', { name: TRY_AGAIN })
    expect(back.getAttribute('aria-disabled')).toBeNull()
    expect(document.activeElement).toBe(heading)
    // One press, its retry answered with a 404, which offers no Try again.
    await user.click(back)
    await waitFor(() => expect(line().textContent).toBe(NOT_AVAILABLE), GRAPH_WAIT)
    await waitFor(() => expect(document.activeElement).toBe(heading), GRAPH_WAIT)
    expect(offeredNow()).toBeNull()
    expect(pages()).toBe(3)
    // Offline and online again: the 404 is sent again, and reads as a first load, since the retry settled.
    act(() => {
      onlineManager.setOnline(false)
    })
    await act(async () => {
      onlineManager.setOnline(true)
    })
    await waitFor(() => expect(hold.waiting()).toBe(1), GRAPH_WAIT)
    await waitFor(() => expect(line().textContent).toBe(LOADING), GRAPH_WAIT)
    expect(offeredNow()).toBeNull()
    hold.release()
    await waitFor(() => expect(line().textContent).toBe(NOT_AVAILABLE), GRAPH_WAIT)
    expect(offeredNow()).toBeNull()
    expect(requests()).toEqual([pageRequest(9), evidenceRequest(9), pageRequest(9), pageRequest(9), pageRequest(9)])
  }, 20_000)

  it('RP-25: a re-read that fails over a loaded page states its failure over the page, and its Try again stays mounted, aria-disabled and focused while it runs, offline too', async () => {
    const user = userEvent.setup()
    const hold = holdRequests()
    // Before the mount: the observer binds the method in its constructor, and the result hands out that copy.
    const refetch = vi.spyOn(QueryObserver.prototype, 'refetch')
    const physical = recorded<GraphRelationshipResponse>('relationship-physical').body
    const Wrapper = graphWrapper(newGraphClient())
    const panel = (open: boolean) => (
      <Wrapper>
        {open && <RelationshipPanel relationshipRef="relationship:3" params={{}} vocabularies={vocabularies()} onClose={() => {}} />}
      </Wrapper>
    )
    const view = render(panel(true))
    await within(screen.getByRole('region')).findByText('2 of 2 results shown.', {}, GRAPH_WAIT)
    await waitFor(() => expect(bodyLine(screen.getByRole('region')).textContent).toBe(''), GRAPH_WAIT)
    // The page's re-reads after the first load: refused; a reconnect's, held then refused; the click's, held then answered; refused; answered.
    let pages = 0
    server.use(http.get(`${REL}:ref`, async () => {
      pages += 1
      const call = pages
      if (call === 2 || call === 3) await hold.wait()
      return call === 3 || call === 5 ? HttpResponse.json(physical) : HttpResponse.json({ detail: 'Request was throttled.' }, { status: 429 })
    }))
    // A reopening after GRAPH_DATA_STALE_MS: the body it mounts re-reads its loaded page.
    view.rerender(panel(false))
    const realNow = Date.now.bind(Date)
    const now = vi.spyOn(Date, 'now').mockImplementation(() => realNow() + 61_000)
    view.rerender(panel(true))
    const region = screen.getByRole('region')
    const heading = within(region).getByRole('heading', { level: 2 })
    await waitFor(() => expect(bodyLine(region).textContent).toBe(THROTTLED), GRAPH_WAIT)
    // The page stays: its summary, not a kept edge's, since the panel was given none.
    expect(within(region).getByText(DIRECT)).toBeInTheDocument()
    expect(within(region).getByText('2 of 2 results shown.')).toBeInTheDocument()
    const offered = within(region).getAllByRole('button', { name: TRY_AGAIN })
    expect(offered).toHaveLength(1)
    const button = offered[0]
    expect(button.getAttribute('aria-disabled')).toBeNull()
    await waitFor(() => expect(requests()).toEqual([pageRequest(3), evidenceRequest(3), pageRequest(3), evidenceRequest(3)]), GRAPH_WAIT)
    // A reconnect re-reads the stale page at once: a re-read no press started, which a press would cancel
    // and send again, so the button is busy through it and keeps focus.
    button.focus()
    act(() => {
      onlineManager.setOnline(false)
    })
    await act(async () => {
      onlineManager.setOnline(true)
    })
    await waitFor(() => expect(hold.waiting()).toBe(1), GRAPH_WAIT)
    expect(pages).toBe(2)
    await waitFor(() => expect(button.getAttribute('aria-disabled')).toBe('true'), GRAPH_WAIT)
    expect(within(region).getByRole('button', { name: TRY_AGAIN })).toBe(button)
    expect(button.style.cursor).toBe('default')
    expect(document.activeElement).toBe(button)
    expect(bodyLine(region).textContent).toBe(THROTTLED)
    expect(within(region).getByText(DIRECT)).toBeInTheDocument()
    const reconnected = refetch.mock.calls.length
    await user.keyboard('{Enter}')
    await pause(300)
    expect(refetch).toHaveBeenCalledTimes(reconnected)
    expect(pages).toBe(2)
    // Refused again, the re-read leaves the failure standing, so the same button is live again, with focus.
    hold.release()
    await waitFor(() => expect(button.getAttribute('aria-disabled')).toBeNull(), GRAPH_WAIT)
    expect(within(region).getByRole('button', { name: TRY_AGAIN })).toBe(button)
    expect(document.activeElement).toBe(button)
    expect(bodyLine(region).textContent).toBe(THROTTLED)
    await user.click(button)
    // Its re-read is held: one more page request, and the same button stays, aria-disabled, with focus, beside the same words.
    await waitFor(() => expect(hold.waiting()).toBe(1), GRAPH_WAIT)
    expect(pages).toBe(3)
    await waitFor(() => expect(button.getAttribute('aria-disabled')).toBe('true'), GRAPH_WAIT)
    expect(within(region).getByRole('button', { name: TRY_AGAIN })).toBe(button)
    expect(button.hasAttribute('disabled')).toBe(false)
    expect(document.activeElement).toBe(button)
    expect(bodyLine(region).textContent).toBe(THROTTLED)
    expect(within(region).getByText(DIRECT)).toBeInTheDocument()
    hold.release()
    await waitFor(() => expect(bodyLine(region).textContent).toBe(''), GRAPH_WAIT)
    await waitFor(() => expect(button.isConnected).toBe(false), GRAPH_WAIT)
    await waitFor(() => expect(document.activeElement).toBe(heading), GRAPH_WAIT)
    expect(within(region).getByText(DIRECT)).toBeInTheDocument()

    // A reconnect once the page is stale again re-reads it, refused again, which offers the button again.
    now.mockImplementation(() => realNow() + 122_000)
    act(() => {
      onlineManager.setOnline(false)
    })
    await act(async () => {
      onlineManager.setOnline(true)
    })
    await waitFor(() => expect(bodyLine(region).textContent).toBe(THROTTLED), GRAPH_WAIT)
    await waitFor(() => expect(requests()).toHaveLength(8), GRAPH_WAIT)
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
    // Offline its re-read waits for the connection, a running retry: busy, beside the failure's words, and nothing is sent.
    expect(again.getAttribute('aria-disabled')).toBe('true')
    expect(again.style.cursor).toBe('default')
    expect(document.activeElement).toBe(again)
    expect(bodyLine(region).textContent).toBe(THROTTLED)
    expect(refetch).toHaveBeenCalledTimes(pressed + 1)
    expect(pages).toBe(4)
    // A second press would cancel the waiting re-read and start another: it calls nothing.
    await user.keyboard('{Enter}')
    await pause(300)
    expect(refetch).toHaveBeenCalledTimes(pressed + 1)
    expect(pages).toBe(4)
    await act(async () => {
      onlineManager.setOnline(true)
    })
    await waitFor(() => expect(bodyLine(region).textContent).toBe(''), GRAPH_WAIT)
    await waitFor(() => expect(again.isConnected).toBe(false), GRAPH_WAIT)
    await waitFor(() => expect(document.activeElement).toBe(heading), GRAPH_WAIT)
    expect(pages).toBe(5)
    now.mockRestore()
    expect(requests()).toEqual([
      pageRequest(3), evidenceRequest(3), pageRequest(3), evidenceRequest(3), pageRequest(3), pageRequest(3), pageRequest(3), evidenceRequest(3),
      pageRequest(3),
    ])
  }, 20_000)

  it('RP-26: over a loaded page, a re-read refused with a 404 offers no Try again and goes with a later 200, a refused 503 is not sent again by a reconnect, and a bad body comes first', async () => {
    const realNow = Date.now.bind(Date)
    const now = vi.spyOn(Date, 'now')
    type Reply = () => Response
    /** relationship:3 loaded on a client of its own, then reopened after GRAPH_DATA_STALE_MS: its page's re-reads take `replies` in turn. */
    const reopenedOver = async (replies: Reply[]) => {
      now.mockImplementation(() => realNow())
      const client = newGraphClient()
      const Wrapper = graphWrapper(client)
      const panel = (open: boolean) => (
        <Wrapper>
          {open && <RelationshipPanel relationshipRef="relationship:3" params={{}} vocabularies={vocabularies()} onClose={() => {}} />}
        </Wrapper>
      )
      const start = log.urls.length
      const view = render(panel(true))
      await within(screen.getByRole('region')).findByText('2 of 2 results shown.', {}, GRAPH_WAIT)
      await waitFor(() => expect(bodyLine(screen.getByRole('region')).textContent).toBe(''), GRAPH_WAIT)
      let calls = 0
      server.use(http.get(`${REL}:ref`, () => {
        const reply = replies[Math.min(calls, replies.length - 1)]
        calls += 1
        return reply()
      }))
      view.rerender(panel(false))
      now.mockImplementation(() => realNow() + 61_000)
      view.rerender(panel(true))
      const settled = async (count: number) => {
        await waitFor(() => {
          const state = client.getQueryCache().findAll({ queryKey: ['graph', 'relationship', 'relationship:3', 'page'] })[0]?.state
          expect([calls, state?.fetchStatus]).toEqual([count, 'idle'])
        }, GRAPH_WAIT)
        await pause(50)
      }
      return { region: screen.getByRole('region'), settled, sent: () => requests().slice(start) }
    }
    const reconnect = async () => {
      act(() => {
        onlineManager.setOnline(false)
      })
      await act(async () => {
        onlineManager.setOnline(true)
      })
    }
    const notFound: Reply = () => HttpResponse.json({ detail: 'Not found.' }, { status: 404 })
    const loaded: Reply = () => HttpResponse.json(recorded<GraphRelationshipResponse>('relationship-physical').body)

    // A 404 offers no Try again; it marked the page stale and graphMayResend sends a not_found again, so a reconnect re-reads it at once.
    let opened = await reopenedOver([notFound, loaded])
    await opened.settled(1)
    expect(bodyLine(opened.region).textContent).toBe(NOT_AVAILABLE)
    expect(within(opened.region).getByText(DIRECT)).toBeInTheDocument()
    expect(within(opened.region).queryByRole('button', { name: TRY_AGAIN })).toBeNull()
    await reconnect()
    await opened.settled(2)
    expect(bodyLine(opened.region).textContent).toBe('')
    expect(within(opened.region).getByText(DIRECT)).toBeInTheDocument()
    expect(opened.sent()).toEqual([pageRequest(3), evidenceRequest(3), pageRequest(3), evidenceRequest(3), pageRequest(3)])
    cleanup()
    server.resetHandlers()

    // A 503 offers Try again, and graphMayResend refuses a too_expensive, so a reconnect sends nothing.
    opened = await reopenedOver([() => HttpResponse.json({ detail: 'Too expensive.' }, { status: 503 })])
    await opened.settled(1)
    expect(bodyLine(opened.region).textContent).toBe(TOO_EXPENSIVE)
    expect(within(opened.region).getByText(DIRECT)).toBeInTheDocument()
    expect(within(opened.region).getAllByRole('button', { name: TRY_AGAIN })).toHaveLength(1)
    await reconnect()
    await pause(600)
    expect(bodyLine(opened.region).textContent).toBe(TOO_EXPENSIVE)
    expect(within(opened.region).getAllByRole('button', { name: TRY_AGAIN })).toHaveLength(1)
    expect(opened.sent()).toEqual([pageRequest(3), evidenceRequest(3), pageRequest(3), evidenceRequest(3)])
    cleanup()
    server.resetHandlers()

    // A 200 the guard refuses gives the bad-body words; a refused re-read over it keeps them, that row coming first.
    opened = await reopenedOver([
      () => HttpResponse.json({ api_version: 'graph/v1' }),
      () => HttpResponse.json({ detail: 'Request was throttled.' }, { status: 429 }),
    ])
    await opened.settled(1)
    expect(bodyLine(opened.region).textContent).toBe(BAD_BODY)
    expect(within(opened.region).queryByText('Summary')).toBeNull()
    expect(within(opened.region).queryByRole('button', { name: TRY_AGAIN })).toBeNull()
    now.mockImplementation(() => realNow() + 122_000)
    await reconnect()
    await opened.settled(2)
    expect(bodyLine(opened.region).textContent).toBe(BAD_BODY)
    expect(within(opened.region).queryByRole('button', { name: TRY_AGAIN })).toBeNull()
    now.mockRestore()
    expect(opened.sent()).toEqual([pageRequest(3), evidenceRequest(3), pageRequest(3), evidenceRequest(3), pageRequest(3), evidenceRequest(3)])
  }, 20_000)
})
