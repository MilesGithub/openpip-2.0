import { InfiniteQueryObserver, QueryObserver, onlineManager } from '@tanstack/react-query'
import { act, cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  evidencePageNoRowsWithNext, extrasEvidencePage, geneticRelationshipPage, hostile, hostileEvidencePage, hostilePhysicalEvidencePage,
  hostileRelationshipPage, makeEvidenceResponse, makeGraphEdge, makeGraphEvidenceRow, makeRelationshipResponse, physicalRelationshipPage,
  regulationRelationshipPage, regulationRowWithMethods, rowWithoutRecord, testScoreTypes, textMiningRow,
} from '../../../mocks/fixtures/typedGraph/extras'
import { server } from '../../../mocks/server'
import { useAuthStore } from '../../../store/authStore'
import { GRAPH_WAIT, graphWrapper, newGraphClient, recorded, recordRequests } from '../../../test/graphHooks'
import type { RequestLog } from '../../../test/graphHooks'
import type { GraphRelationshipParams } from '../../../api/graph/params'
import type {
  GraphEvidenceResponse, GraphEvidenceRow, GraphNetworkResponse, GraphRelationshipResponse, GraphVocabularies,
} from '../types'
import { graphOfNetwork } from '../network/graph'
import { RelationshipPanel } from './RelationshipPanel'
import { TypedSelectionPanel } from './TypedSelectionPanel'

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
const LOADING_EVIDENCE = 'Loading the evidence…'
const LOADING_MORE = 'Loading more evidence…'
const SHOW_MORE = 'Show more'
const TRY_EVIDENCE = 'Try loading the evidence again'
const BAD_BODY = "The server's answer could not be read."
const TOO_EXPENSIVE = 'This page of evidence took too long to compute. Narrow the filters before trying again.'
const NO_EFFECT = 'not applicable: this type states no effect'
const NOT_COMPARABLE = '; its type says its values are not comparable across datasets'
const SCORES_NOTE = 'Each score is read within its own type; scores of different types are never pooled.'
const RAW_UNAVAILABLE = "the source record's columns are not available here"
const REFUSED = 'The server refused the request for this evidence.'
const THROTTLED = 'Too many requests. Try again shortly.'
const NOT_AVAILABLE = "This relationship's evidence is not available to you."
const UNREACHABLE = 'The server could not be reached.'
const TOO_LONG = 'The request for this evidence would be too long to send. Use fewer filters.'

const REL = '/api/graph/v1/relationships/'
const pageRequest = (id: number, search = '') => [`${REL}relationship%3A${id}`, search]
const evidenceRequest = (id: number, search = '') => [`${REL}relationship%3A${id}/evidence`, search]
const requests = () => log.urls.map((url) => [url.pathname, url.search])

const vocabularies = () => recorded<GraphVocabularies>('vocabularies').body

function openPanel(ref: string, params: GraphRelationshipParams, options: { vocab?: GraphVocabularies; pageSize?: number } = {}) {
  return render(
    <RelationshipPanel
      relationshipRef={ref}
      params={params}
      vocabularies={options.vocab ?? vocabularies()}
      evidencePageSize={options.pageSize}
      onClose={() => {}}
    />,
    { wrapper: graphWrapper(newGraphClient()) },
  )
}

/** Through the dispatcher, as a page opens a relationship from a recorded network. */
function openFrom(name: string, id: string) {
  const graph = graphOfNetwork(recorded<GraphNetworkResponse>(name).body)
  const record = graph.edges.find((edge) => edge.id === id)
  if (record === undefined) throw new Error(`no ${id} in ${name}`)
  return render(
    <TypedSelectionPanel selection={{ kind: 'edge', id, record }} graph={graph} vocabularies={vocabularies()} onClose={() => {}} />,
    { wrapper: graphWrapper(newGraphClient()) },
  )
}

type Reply = () => Response | Promise<Response>

/** relationship:901's page, and its evidence pages by the `after` each is asked for ('' for the first). */
function stubPages(pages: [string, Reply][], relationship: GraphRelationshipResponse = makeRelationshipResponse()) {
  const byAfter = new Map(pages)
  server.use(
    http.get(`${REL}:ref`, () => HttpResponse.json(relationship)),
    http.get(`${REL}:ref/evidence`, ({ request }) => {
      const reply = byAfter.get(new URL(request.url).searchParams.get('after') ?? '')
      return reply === undefined ? HttpResponse.json({ detail: 'No such page.' }, { status: 501 }) : reply()
    }),
  )
}

const page = (body: Partial<GraphEvidenceResponse>): Reply => () => HttpResponse.json(makeEvidenceResponse(body))
const totalOfTwo = () => makeRelationshipResponse({
  edge: makeGraphEdge({ outcome_counts: { supported: 2, tested_absent: 0, inconclusive: 0, not_significant: 0 } }),
})
const firstOfTwo = page({ evidence: [regulationRowWithMethods], page: { page_size: 50, next: 'evidence:951' } })
const totalOf = (count: number) => makeRelationshipResponse({
  edge: makeGraphEdge({ outcome_counts: { supported: count, tested_absent: 0, inconclusive: 0, not_significant: 0 } }),
})
const pageOf = (rows: GraphEvidenceRow[], next: string | null): Reply => page({ evidence: rows, page: { page_size: 50, next } })
const refusal = (status: 429 | 404): Reply => () => HttpResponse.json(
  { detail: status === 429 ? 'Request was throttled.' : 'Not found.' }, { status },
)
const panel901 = () => <RelationshipPanel relationshipRef="relationship:901" params={{}} vocabularies={vocabularies()} onClose={() => {}} />

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

/** The list's region is a div; the body's status line is a paragraph. */
function listStatus(region: HTMLElement): HTMLElement {
  const regions = within(region).getAllByRole('status').filter((status) => status.tagName === 'DIV')
  expect(regions).toHaveLength(1)
  return regions[0]
}

function rowOf(region: HTMLElement, id: string): HTMLElement {
  const items = within(region).getAllByRole('listitem').filter((item) => (item.textContent ?? '').includes(`(${id})`))
  expect(items, id).toHaveLength(1)
  return items[0]
}

function paragraphTexts(region: HTMLElement): string[] {
  return Array.from(region.querySelectorAll('p')).map((paragraph) => paragraph.textContent ?? '')
}

/** The lines of the list's region, in order. */
function listLines(region: HTMLElement): string[] {
  return Array.from(listStatus(region).children).map((line) => line.textContent ?? '')
}

/** The list's controls, every button after its region, each marked while it is aria-disabled. */
function listControls(region: HTMLElement): string[] {
  const status = listStatus(region)
  return within(region).queryAllByRole('button')
    .filter((button) => (status.compareDocumentPosition(button) & Node.DOCUMENT_POSITION_FOLLOWING) !== 0)
    .map((button) => `${button.textContent ?? ''}${button.getAttribute('aria-disabled') === 'true' ? ' (aria-disabled)' : ''}`)
}

/** The list's lines that count each release's results, in document order. */
function releaseLines(region: HTMLElement): string[] {
  return within(region).queryAllByRole('listitem')
    .map((item) => item.textContent ?? '')
    .filter((text) => /: \d+ results? \(assertions?\)$/.test(text))
}

/** A re-read of the loaded pages, as a reopening sends once the data has gone stale. */
async function rereadEvidence(client: ReturnType<typeof newGraphClient>, ref: string): Promise<void> {
  await act(async () => {
    await client.invalidateQueries({ queryKey: ['graph', 'relationship', ref, 'evidence'] })
  })
}

/** Waits on the query, not on the words, so a list that misreads is read too, instead of a wait that times out. */
async function evidenceSettled(client: ReturnType<typeof newGraphClient>, ref: string, status: 'error' | 'success'): Promise<void> {
  await waitFor(() => {
    const state = client.getQueryCache().findAll({ queryKey: ['graph', 'relationship', ref, 'evidence'] })[0]?.state
    expect([state?.status, state?.fetchStatus]).toEqual([status, 'idle'])
  }, GRAPH_WAIT)
  await pause(50)
}

function rowTitle(row: HTMLElement): string {
  return row.querySelector('p strong')?.textContent ?? ''
}

function terms(row: HTMLElement): string[] {
  return Array.from(row.querySelectorAll(':scope > dl > dt')).map((dt) => dt.textContent ?? '')
}

function fieldLines(row: HTMLElement, term: string): string[] {
  const found = Array.from(row.querySelectorAll(':scope > dl > dt')).filter((dt) => dt.textContent === term)
  expect(found, term).toHaveLength(1)
  const detail = found[0].nextElementSibling
  if (detail === null || detail.tagName !== 'DD') throw new Error(`no detail after ${term}`)
  return Array.from(detail.children).filter((line) => line.tagName === 'SPAN' || line.tagName === 'A').map((line) => line.textContent ?? '')
}

describe('EvidenceList', () => {
  it('EL-1: relationship:9 (evidence-mixed): the row of evidence:21, and the effect of evidence:22', async () => {
    openPanel('relationship:9', {})
    const region = screen.getByRole('region')
    await within(region).findByText('2 of 2 results shown.', {}, GRAPH_WAIT)
    const row = rowOf(region, 'evidence:21')
    expect(rowTitle(row)).toBe('Supporting result')
    expect(fieldLines(row, 'Release')).toEqual(['Synthetic regulation example v2 (synthetic)'])
    expect(fieldLines(row, 'Evidence basis')).toEqual(['experimental: rests on an experiment'])
    expect(fieldLines(row, 'Assertion method')).toEqual(['manual assertion: a person reviewed the evidence and made the statement'])
    expect(fieldLines(row, 'Effect')).toEqual(['activates'])
    expect(fieldLines(row, 'Effect as stated')).toEqual(["source's wording: not recorded"])
    expect(fieldLines(row, 'Publications')).toEqual([
      'reported in: SYN demonstration paper 1 (synthetic: not a real publication) (synthetic)', '2026', 'local key syn-paper-1',
    ])
    expect(fieldLines(row, 'Source record')).toEqual(['syn-03, line 4 (evidence:21)'])
    expect(within(row).getByRole('button', { name: "Source record's columns: syn-03, line 4 (evidence:21)" })).toBeInTheDocument()
    expect(fieldLines(rowOf(region, 'evidence:22'), 'Effect')).toEqual(['inhibits'])
    expect(requests()).toEqual([pageRequest(9), evidenceRequest(9)])
  }, 20_000)

  it('EL-2: relationship:13 (evidence-scores) with no edge known: each score within its type, no sign field', async () => {
    openPanel('relationship:13', {})
    const region = screen.getByRole('region')
    await within(region).findByText('1 result shown.', {}, GRAPH_WAIT)
    await within(region).findByText('This relationship could not be loaded (HTTP 501).', {}, GRAPH_WAIT)
    const row = rowOf(region, 'evidence:27')
    expect(fieldLines(row, 'Scores')).toEqual([
      `Synthetic confidence (synthetic:confidence): 0.8; a probability, higher is stronger${NOT_COMPARABLE}`,
      `Synthetic rank (synthetic:rank): 1; a rank, lower is stronger${NOT_COMPARABLE}`,
      SCORES_NOTE,
    ])
    expect(fieldLines(row, 'Publications')).toEqual(['no publication recorded'])
    expect(fieldLines(row, 'Evidence basis')).toEqual(['computational prediction: rests on a computational prediction, not an experiment'])
    expect(fieldLines(row, 'Assertion method')).toEqual(["automatic assertion: made by a program without a person's review"])
    expect(terms(row)).not.toContain('Genetic sign')
    expect(requests()).toEqual([pageRequest(13), evidenceRequest(13)])
  }, 20_000)

  it('EL-3: relationship:3 (evidence-physical): interaction types and levels, and no effect on a physical row', async () => {
    openPanel('relationship:3', {})
    const region = screen.getByRole('region')
    await within(region).findByText('2 of 2 results shown.', {}, GRAPH_WAIT)
    const association = rowOf(region, 'evidence:28')
    expect(fieldLines(association, 'Interaction type')).toEqual(['physical association (MI:0915)'])
    expect(fieldLines(association, 'Interaction level')).toEqual(['physical association'])
    expect(fieldLines(association, 'Effect')).toEqual([NO_EFFECT])
    const direct = rowOf(region, 'evidence:29')
    expect(fieldLines(direct, 'Interaction type')).toEqual(['direct interaction (MI:0407)'])
    expect(fieldLines(direct, 'Interaction level')).toEqual(['direct interaction'])
    expect(fieldLines(direct, 'Effect')).toEqual([NO_EFFECT])
    expect(Array.from(region.querySelectorAll('p')).filter((paragraph) => (paragraph.textContent ?? '').startsWith('Effect: '))).toEqual([])
    expect(requests()).toEqual([pageRequest(3), evidenceRequest(3)])
  }, 20_000)

  it('EL-4: relationship:4 from network-all-edges, and a tested-absent row of a type with effect polarity', async () => {
    openFrom('network-all-edges', 'relationship:4')
    let region = screen.getByRole('region')
    await within(region).findByText('2 of 2 results shown.', {}, GRAPH_WAIT)
    const tested = rowOf(region, 'evidence:36')
    expect(rowTitle(tested)).toBe('Tested, not detected')
    expect(fieldLines(tested, 'Effect')).toEqual([NO_EFFECT])
    expect(fieldLines(tested, 'Interaction level tested')).toEqual(['direct interaction'])
    expect(terms(tested)).not.toContain('Interaction level')
    const supported = rowOf(region, 'evidence:35')
    expect(fieldLines(supported, 'Interaction level')).toEqual(['physical interaction, level not stated'])
    expect(fieldLines(supported, 'Participants')).toContain('SYN-P3 (participant, as written), resolved to SYN-SHARED by its primary identifier')
    // Each release's line counts every outcome's results: a supporting-only count would read 1 here, and 0 below.
    await waitFor(() => expect(releaseLines(region)).toEqual(['Synthetic regulation example v2 (synthetic): 2 results (assertions)']), GRAPH_WAIT)
    expect(requests()).toEqual([pageRequest(4, '?outcome=all'), evidenceRequest(4)])
    cleanup()
    openPanel('relationship:7', { outcome: 'all' })
    region = screen.getByRole('region')
    await within(region).findByText('1 of 1 result shown.', {}, GRAPH_WAIT)
    expect(fieldLines(rowOf(region, 'evidence:25'), 'Effect')).toEqual(['none: only a supporting result states an effect'])
    await waitFor(() => expect(releaseLines(region)).toEqual(['Synthetic regulation example v2 (synthetic): 1 result (assertion)']), GRAPH_WAIT)
    expect(requests().slice(2)).toEqual([pageRequest(7, '?outcome=all'), evidenceRequest(7)])
  }, 20_000)

  it("EL-5: 17.3's rows: methods, terms, thresholds, scores read within their type, contexts, participants, papers", async () => {
    const base = vocabularies()
    const vocab: GraphVocabularies = { ...base, score_types: [...base.score_types, ...testScoreTypes] }
    // 17.3's list of the three pages: each is served by its own relationship's ref, under that relationship's page.
    const served: Record<string, [GraphRelationshipResponse, string]> = {
      'relationship:901': [regulationRelationshipPage, '5 of 5 results shown.'],
      'relationship:930': [physicalRelationshipPage, '3 of 3 results shown.'],
      'relationship:906': [geneticRelationshipPage, '1 of 1 result shown.'],
    }
    const regulator = (stated: string, name: string, resolution: string) => `${stated} (regulator, as written), resolved to ${name} ${resolution}`
    const target = (stated: string, name: string, resolution: string) => `${stated} (target, as written), resolved to ${name} ${resolution}`
    const primary = 'by its primary identifier'
    for (const evidence of extrasEvidencePage) {
      const ref = evidence.query.relationship_ref
      const entry = served[ref]
      if (entry === undefined) throw new Error(`EL-5 serves no relationship page for ${ref}`)
      const [relationship, count] = entry
      const id = Number(ref.slice('relationship:'.length))
      server.use(http.get(`${REL}:ref`, () => HttpResponse.json(relationship)), http.get(`${REL}:ref/evidence`, () => HttpResponse.json(evidence)))
      const view = openPanel(ref, {}, { vocab })
      const region = screen.getByRole('region')
      await within(region).findByText(count, {}, GRAPH_WAIT)
      if (id === 901) {
        const methods = rowOf(region, 'evidence:951')
        expect(terms(methods)).toEqual([
          'Release', 'Evidence basis', 'Assertion method', 'Effect', 'Effect as stated', 'Relationship as stated', 'Methods',
          'ECO evidence code', 'Scores', 'Qualifiers', 'Contexts', 'Participants', 'Evidence group', 'Publications', 'Source record',
        ])
        expect(fieldLines(methods, 'Release')).toEqual(['Synthetic extras x1 (synthetic)'])
        expect(fieldLines(methods, 'Effect as stated')).toEqual(["source's wording: up-regulates"])
        expect(fieldLines(methods, 'Relationship as stated')).toEqual(['activates transcription of'])
        expect(fieldLines(methods, 'Methods')).toEqual([
          'detection method: MI:0402', 'participant identification method: MI:0102', 'evidence code: ECO:0000353',
        ])
        expect(fieldLines(methods, 'ECO evidence code')).toEqual(['ECO:0000353'])
        expect(fieldLines(methods, 'Scores')).toEqual(['none stated'])
        expect(fieldLines(methods, 'Qualifiers')).toEqual(['assay_note: two replicates'])
        expect(fieldLines(methods, 'Contexts')).toEqual([
          'tissue: liver (as written: liver)',
          'host taxon: synthetic construct (taxid 32630) (as written: 32630)',
          'assay system: MI:0492 (as written: MI:0492)',
        ])
        expect(fieldLines(methods, 'Participants')).toEqual([
          regulator('SYN-N901-ALIAS', 'SYN-N901', 'through a symbol or alias that only this entity holds'),
          target('SYN-N902', 'SYN-N902', 'through a cross-reference that only this entity holds'),
        ])
        expect(fieldLines(methods, 'Evidence group')).toEqual(['g-1: results the source groups as one observation in this release'])
        expect(fieldLines(methods, 'Publications')).toEqual([
          'reported in: Hand-written paper 1 (synthetic: not a real publication) (synthetic)', 'Test A, Test B, Test Journal, 2026',
          'local key extras-paper-1', 'https://example.org/paper-1',
          'method reference: title not recorded (synthetic)', 'local key extras-method-1',
          'cited: Cited test paper (synthetic)', 'local key extras-cited-1', 'https://example.org/paper-2',
        ])
        const links = Array.from(methods.querySelectorAll('a[href]')).map((link) => [link.getAttribute('href'), link.textContent])
        expect(links).toEqual([['https://example.org/paper-1', 'https://example.org/paper-1'], ['https://example.org/paper-2', 'https://example.org/paper-2']])
        const failed = rowOf(region, 'evidence:953')
        expect(rowTitle(failed)).toBe('Not significant at the stated threshold (not among the requested outcomes)')
        expect(fieldLines(failed, 'Evidence basis')).toEqual(['statistical: rests on a statistical analysis'])
        expect(fieldLines(failed, 'Assertion method')).toEqual(["automatic assertion: made by a program without a person's review"])
        expect(fieldLines(failed, 'Effect')).toEqual(['none: only a supporting result states an effect'])
        expect(fieldLines(failed, 'Scores')).toEqual([
          `Test p-value (test:p_value): <2.2e-16 (as written; no exact number); a p-value, lower is stronger${NOT_COMPARABLE}`,
          `Test effect size (test:effect_size): 0.12 log2; a continuous value, signed; null model: multiplicative${NOT_COMPARABLE}`,
          SCORES_NOTE,
        ])
        expect(fieldLines(failed, 'Significance threshold')).toEqual([
          'As the source wrote them: test:effect_size abs>= 0.5; test:p_value < 0.05. A result is significant only when it meets every criterion; this one does not.',
        ])
        expect(fieldLines(failed, 'Participants')).toEqual([regulator('SYN-N901', 'SYN-N901', primary), target('SYN-N902', 'SYN-N902', primary)])
        expect(fieldLines(failed, 'Publications')).toEqual(['no publication recorded'])
        const inconclusive = rowOf(region, 'evidence:954')
        expect(rowTitle(inconclusive)).toBe('Inconclusive result (not among the requested outcomes)')
        expect(fieldLines(inconclusive, 'Evidence basis')).toEqual(['computational inference: inferred computationally, not an experiment'])
        expect(fieldLines(inconclusive, 'Assertion method')).toEqual(['unknown: not stated'])
        const mined = rowOf(region, 'evidence:955')
        expect(fieldLines(mined, 'Evidence basis')).toEqual(['text mining: found by mining text'])
        expect(fieldLines(mined, 'Effect')).toEqual(['not stated by the source'])
        expect(fieldLines(mined, 'Participants')).toEqual([regulator('SYN-N901', 'SYN-N901', '(source local)'), target('SYN-N902', 'SYN-N902', primary)])
        const unknown = rowOf(region, 'evidence:956')
        expect(fieldLines(unknown, 'Evidence basis')).toEqual(['unknown: unknown or not stated'])
        expect(fieldLines(unknown, 'Assertion method')).toEqual(['unknown: not stated'])
        expect(fieldLines(unknown, 'Effect')).toEqual(['effect unknown'])
        // No other row renders this page's summary, so its line of results neither for nor against is read here.
        expect(paragraphTexts(region)).toContain(
          'Results neither for nor against this relationship: 1 inconclusive result; 1 result not significant at the stated threshold',
        )
      } else if (id === 930) {
        const labelled = rowOf(region, 'evidence:961')
        expect(fieldLines(labelled, 'Release')).toEqual(['Test labelled release x1'])
        expect(fieldLines(labelled, 'Effect')).toEqual([NO_EFFECT])
        expect(fieldLines(labelled, 'Interaction type')).toEqual(['physical association (MI:0915)'])
        expect(fieldLines(labelled, 'Interaction level')).toEqual(['physical association'])
        expect(fieldLines(labelled, 'Methods')).toEqual(['detection method: two hybrid (MI:0018)'])
        expect(fieldLines(labelled, 'ECO evidence code')).toEqual(['physical interaction evidence used in manual assertion (ECO:0000353)'])
        expect(fieldLines(labelled, 'Participants')).toEqual([
          'SYN-N903 (participant, as written), resolved to SYN-N901 on its protein entry: the source named an isoform; the isoform named: entity:903',
          'SYN-N902-CURATED (participant, as written), resolved to SYN-N902 by a reviewed resolution file',
        ])
        const assigned = rowOf(region, 'evidence:962')
        expect(terms(assigned)).not.toContain('Interaction type')
        expect(fieldLines(assigned, 'Interaction level')).toEqual(['co-complex association, assigned by openPIP from the stated assay'])
        const stated = rowOf(region, 'evidence:963')
        expect(fieldLines(stated, 'Interaction type')).toEqual(['association (MI:0914)'])
        expect(fieldLines(stated, 'Interaction level')).toEqual(['association (co-purification)'])
      } else {
        const sign = rowOf(region, 'evidence:971')
        expect(fieldLines(sign, 'Effect')).toEqual([NO_EFFECT])
        expect(fieldLines(sign, 'Genetic sign')).toEqual(['negative (aggravating)'])
        expect(fieldLines(sign, 'Scores')).toEqual([
          `Test effect size (test:effect_size): -0.4 log2; a continuous value, signed; null model: multiplicative${NOT_COMPARABLE}`,
          SCORES_NOTE,
        ])
        // Nor this one's: its sign lines, in order, with what a sign means.
        const signLines = [
          'Genetic sign: negative (aggravating)',
          'Supporting results in view: 1 states a negative (aggravating) sign',
          'A genetic sign is read relative to the null model of the score that states it; it is not inhibition, and not a result tested and not detected.',
        ]
        expect(paragraphTexts(region).filter((text) => signLines.includes(text))).toEqual(signLines)
      }
      expect(requests().slice(-2)).toEqual([pageRequest(id), evidenceRequest(id)])
      view.unmount()
      server.resetHandlers()
    }
    expect(requests()).toEqual([pageRequest(901), evidenceRequest(901), pageRequest(930), evidenceRequest(930), pageRequest(906), evidenceRequest(906)])
  }, 20_000)

  it('EL-6: the hostile pages render every field as text, link only http(s) addresses, and clip a long value', async () => {
    const user = userEvent.setup()
    const opened: [number, GraphRelationshipResponse, GraphEvidenceResponse, string[]][] = [
      [901, hostileRelationshipPage, hostileEvidencePage, [
        'definition', 'nodes[0].label', 'nodes[1].label', 'qualifiers.key', 'qualifiers.value', 'effect_as_stated', 'relationship_as_stated',
        'participants[0].identifier_as_stated', 'participants[1].identifier_as_stated', 'methods[0].label', 'scores[0].label',
        'scores[0].raw_value', 'scores[0].unit', 'contexts[0].label', 'contexts[0].raw_value', 'evidence_group', 'record.key',
        'publications[0].title', 'publications[0].authors', 'publications[0].journal', 'raw.key', 'raw.value', 'row.qualifiers.note',
        'dataset.title', 'version', 'licence.name', 'licence.spdx_id', 'attribution', 'changes_note',
      ]],
      [930, physicalRelationshipPage, hostilePhysicalEvidencePage, ['interaction_type.label', 'eco_term.label']],
    ]
    for (const [id, relationship, evidence, paths] of opened) {
      server.use(http.get(`${REL}:ref`, () => HttpResponse.json(relationship)), http.get(`${REL}:ref/evidence`, () => HttpResponse.json(evidence)))
      const view = openPanel(`relationship:${id}`, {})
      const region = screen.getByRole('region')
      await within(region).findByText(/results? shown\.$/, {}, GRAPH_WAIT)
      await waitFor(() => expect(within(region).getAllByRole('status')[0].textContent).toBe(''), GRAPH_WAIT)
      // jsdom fetches no image and runs no inserted script, so a parsed element is the only proof.
      expect(view.container.querySelector('[data-hostile]')).toBeNull()
      for (const toggle of within(region).queryAllByRole('button', { name: /^Source record's columns: / })) await user.click(toggle)
      expect(view.container.querySelector('[data-hostile]')).toBeNull()
      const text = view.container.textContent ?? ''
      expect(paths.filter((path) => !text.includes(hostile(path)))).toEqual([])
      expect(Array.from(view.container.querySelectorAll('a[href]')).filter((link) => !/^https?:\/\//.test(link.getAttribute('href') ?? ''))).toEqual([])
      if (id === 901) {
        // 17.3 gives both addresses the same string, so each is read from its own field.
        const licence = Array.from(region.querySelectorAll('dt')).filter((term) => term.textContent === 'Licence')
        expect(licence.map((term) => term.nextElementSibling?.textContent ?? null)).toEqual([
          `${hostile('licence.name')} (${hostile('licence.spdx_id')}) javascript:alert(1)`,
        ])
        expect(fieldLines(rowOf(region, 'evidence:970'), 'Publications')).toContain('javascript:alert(1)')
        expect(Array.from(view.container.querySelectorAll('a')).filter((link) => (link.textContent ?? '').includes('javascript:'))).toEqual([])
        for (const marker of ['edge', 'raw', 'q']) {
          const letter = marker === 'edge' ? 'z' : marker === 'raw' ? 'x' : 'y'
          expect(text).toContain(`{"dangerouslySetInnerHTML":{"__html":"<em data-hostile=\\"${marker}\\">${letter}</em>"}}`)
        }
        expect(text).toContain(`${'x'.repeat(1000)}… (9000 more characters not shown)`)
        expect(text).not.toContain('x'.repeat(1001))
      }
      expect(requests().slice(-2)).toEqual([pageRequest(id), evidenceRequest(id)])
      view.unmount()
      server.resetHandlers()
    }
    expect(requests()).toEqual([pageRequest(901), evidenceRequest(901), pageRequest(930), evidenceRequest(930)])
  }, 20_000)

  it('EL-7: one paging button, aria-disabled while its page runs, and the count in the region after the rows', async () => {
    const user = userEvent.setup()
    const hold = holdRequests()
    server.use(http.get(`${REL}:ref/evidence`, async ({ request }) => {
      if (new URL(request.url).searchParams.get('after') === null) return undefined
      await hold.wait()
      return HttpResponse.json(recorded<GraphEvidenceResponse>('evidence-mixed-page-2').body)
    }))
    openPanel('relationship:9', { outcome: 'all' }, { pageSize: 1 })
    const region = screen.getByRole('region')
    await within(region).findByText('1 of 2 results shown.', {}, GRAPH_WAIT)
    const more = within(region).getByRole('button', { name: SHOW_MORE })
    await user.click(more)
    // Page 2 is held while the row reads its in-flight state.
    await waitFor(() => expect(hold.waiting()).toBe(1), GRAPH_WAIT)
    await waitFor(() => expect(more.getAttribute('aria-disabled')).toBe('true'), GRAPH_WAIT)
    expect(more.hasAttribute('disabled')).toBe(false)
    expect(within(listStatus(region)).getByText(LOADING_MORE)).toBeInTheDocument()
    hold.release()
    await within(listStatus(region)).findByText('2 of 2 results shown.', {}, GRAPH_WAIT)
    const rows = within(region).getAllByRole('list').find((list) => within(list).queryAllByText(/\(evidence:2[12]\)$/).length > 0)
    if (rows === undefined) throw new Error('no list of rows')
    expect(rows.compareDocumentPosition(listStatus(region)) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(within(region).queryByRole('button', { name: SHOW_MORE })).toBeNull()
    expect(within(region).queryByRole('button', { name: TRY_EVIDENCE })).toBeNull()
    expect(requests()).toEqual([pageRequest(9, '?outcome=all'), evidenceRequest(9, '?page_size=1'), evidenceRequest(9, '?after=evidence:21&page_size=1')])
  }, 20_000)

  it('EL-8: two pages of two releases: each release listed once, each row naming its own', async () => {
    const user = userEvent.setup()
    expect(recorded<GraphEvidenceResponse>('evidence-effect-both-releases-page-1').body.releases.map((release) => release.id)).toEqual(['release:1'])
    expect(recorded<GraphEvidenceResponse>('evidence-effect-both-releases-page-2').body.releases.map((release) => release.id)).toEqual(['release:2'])
    openPanel('relationship:6', { releases: ['release:1', 'release:2'] }, { pageSize: 1 })
    const region = screen.getByRole('region')
    await within(region).findByText('1 of 2 results shown.', {}, GRAPH_WAIT)
    await user.click(within(region).getByRole('button', { name: SHOW_MORE }))
    await within(region).findByText('2 of 2 results shown.', {}, GRAPH_WAIT)
    expect(fieldLines(rowOf(region, 'evidence:1'), 'Release')).toEqual(['Synthetic regulation example v1 (superseded) (synthetic)'])
    expect(fieldLines(rowOf(region, 'evidence:19'), 'Release')).toEqual(['Synthetic regulation example v2 (synthetic)'])
    const listed = Array.from(region.querySelectorAll('dl'))
      .filter((list) => Array.from(list.querySelectorAll('dt')).some((dt) => dt.textContent === 'Status'))
      .map((list) => list.querySelector('dd')?.textContent)
    expect(listed).toEqual(['Synthetic regulation example v1', 'Synthetic regulation example v2'])
    // One line per entry of the page's by_release, in its order, each worded as its release.
    await waitFor(() => expect(releaseLines(region)).toEqual([
      'Synthetic regulation example v1 (superseded) (synthetic): 1 result (assertion)',
      'Synthetic regulation example v2 (synthetic): 1 result (assertion)',
    ]), GRAPH_WAIT)
    const both = '?releases=release:1,release:2'
    expect(requests()).toEqual([
      pageRequest(6, both), evidenceRequest(6, '?page_size=1&releases=release:1,release:2'),
      evidenceRequest(6, '?after=evidence:1&page_size=1&releases=release:1,release:2'),
    ])
  }, 20_000)

  it('EL-9: a page with no row and a next is not the end, nor is a short page', async () => {
    const user = userEvent.setup()
    stubPages([
      ['', () => HttpResponse.json({
        ...evidencePageNoRowsWithNext,
        query: { ...evidencePageNoRowsWithNext.query, relationship_ref: 'relationship:901', after: null },
      })],
      ['evidence:22', page({ evidence: [regulationRowWithMethods] })],
    ])
    const view = openPanel('relationship:901', {})
    let region = screen.getByRole('region')
    await within(region).findByText('0 of 1 result shown.', {}, GRAPH_WAIT)
    expect(within(region).queryByText('No result of this relationship passes the current filters.')).toBeNull()
    await user.click(within(region).getByRole('button', { name: SHOW_MORE }))
    await within(region).findByText('1 of 1 result shown.', {}, GRAPH_WAIT)
    expect(rowTitle(rowOf(region, 'evidence:951'))).toBe('Supporting result')
    expect(requests()).toEqual([pageRequest(901), evidenceRequest(901), evidenceRequest(901, '?after=evidence:22')])
    view.unmount()
    server.resetHandlers()
    stubPages([
      ['', firstOfTwo],
      ['evidence:951', page({ evidence: [textMiningRow] })],
    ], totalOfTwo())
    openPanel('relationship:901', {})
    region = screen.getByRole('region')
    await within(region).findByText('1 of 2 results shown.', {}, GRAPH_WAIT)
    await user.click(within(region).getByRole('button', { name: SHOW_MORE }))
    await within(region).findByText('2 of 2 results shown.', {}, GRAPH_WAIT)
    expect(rowTitle(rowOf(region, 'evidence:955'))).toBe('Supporting result')
    expect(requests().slice(3)).toEqual([pageRequest(901), evidenceRequest(901), evidenceRequest(901, '?after=evidence:951')])
  }, 20_000)

  it('EL-10: a failed later page: its words, one control that retries its own cursor with focus kept; a refused one leaves none', async () => {
    const user = userEvent.setup()
    const hold = holdRequests()
    let later = 0
    stubPages([
      ['', firstOfTwo],
      ['evidence:951', async () => {
        later += 1
        await hold.wait()
        return HttpResponse.json({ detail: 'Too expensive.' }, { status: 503 })
      }],
    ], totalOfTwo())
    const view = openPanel('relationship:901', {})
    let region = screen.getByRole('region')
    await within(region).findByText('1 of 2 results shown.', {}, GRAPH_WAIT)
    const control = within(region).getByRole('button', { name: SHOW_MORE })
    await user.click(control)
    // The first request for page 2, held: focus stays on the control through it.
    await waitFor(() => expect(hold.waiting()).toBe(1), GRAPH_WAIT)
    await waitFor(() => expect(control.getAttribute('aria-disabled')).toBe('true'), GRAPH_WAIT)
    expect(document.activeElement).toBe(control)
    hold.release()
    await within(listStatus(region)).findByText(TOO_EXPENSIVE, {}, GRAPH_WAIT)
    expect(control.textContent).toBe(TRY_EVIDENCE)
    expect(control.getAttribute('aria-disabled')).toBeNull()
    expect(within(region).queryByRole('button', { name: SHOW_MORE })).toBeNull()
    expect(within(region).getAllByRole('button', { name: TRY_EVIDENCE })).toEqual([control])
    expect(document.activeElement).toBe(control)
    expect(rowOf(region, 'evidence:951')).toBeInTheDocument()
    await user.click(control)
    // Its retry, held: the same control, aria-disabled, with focus, and the page's own cursor sent again.
    await waitFor(() => expect(hold.waiting()).toBe(1), GRAPH_WAIT)
    expect(later).toBe(2)
    await waitFor(() => expect(control.getAttribute('aria-disabled')).toBe('true'), GRAPH_WAIT)
    expect(document.activeElement).toBe(control)
    expect(within(listStatus(region)).getByText(LOADING_MORE)).toBeInTheDocument()
    expect(rowOf(region, 'evidence:951')).toBeInTheDocument()
    hold.release()
    await waitFor(() => expect(control.getAttribute('aria-disabled')).toBeNull(), GRAPH_WAIT)
    await within(listStatus(region)).findByText(TOO_EXPENSIVE, {}, GRAPH_WAIT)
    expect(document.activeElement).toBe(control)
    expect(within(listStatus(region)).getByText('1 of 2 results shown.')).toBeInTheDocument()
    expect(requests()).toEqual([
      pageRequest(901), evidenceRequest(901), evidenceRequest(901, '?after=evidence:951'), evidenceRequest(901, '?after=evidence:951'),
    ])

    view.unmount()
    server.resetHandlers()
    const from = log.urls.length
    let refused = 0
    stubPages([
      ['', firstOfTwo],
      ['evidence:951', () => {
        refused += 1
        return HttpResponse.json({ detail: 'Bad request.', errors: [] }, { status: 400 })
      }],
    ], totalOfTwo())
    openPanel('relationship:901', {})
    region = screen.getByRole('region')
    await within(region).findByText('1 of 2 results shown.', {}, GRAPH_WAIT)
    await user.click(within(region).getByRole('button', { name: SHOW_MORE }))
    await within(listStatus(region)).findByText(REFUSED, {}, GRAPH_WAIT)
    expect(within(region).queryByRole('button', { name: SHOW_MORE })).toBeNull()
    expect(within(region).queryByRole('button', { name: TRY_EVIDENCE })).toBeNull()
    await waitFor(() => expect(document.activeElement?.textContent).toBe('1 of 2 results shown.'), GRAPH_WAIT)
    expect(document.activeElement?.tagName).toBe('P')
    await pause(300)
    expect(refused).toBe(1)
    expect(requests().slice(from)).toEqual([pageRequest(901), evidenceRequest(901), evidenceRequest(901, '?after=evidence:951')])
  }, 20_000)

  it('EL-11: two activations within one double click send one later page; the button is only aria-disabled meanwhile, and its handler returns at once', async () => {
    const user = userEvent.setup()
    const hold = holdRequests()
    let later = 0
    stubPages([
      ['', firstOfTwo],
      ['evidence:951', async () => {
        later += 1
        await hold.wait()
        return HttpResponse.json(makeEvidenceResponse({ evidence: [textMiningRow] }))
      }],
    ], totalOfTwo())
    // Before the mount: the observer binds the method in its constructor, and the result hands out that copy.
    const fetchNextPage = vi.spyOn(InfiniteQueryObserver.prototype, 'fetchNextPage')
    openPanel('relationship:901', {})
    const region = screen.getByRole('region')
    await within(region).findByText('1 of 2 results shown.', {}, GRAPH_WAIT)
    const more = within(region).getByRole('button', { name: SHOW_MORE })
    await user.dblClick(more)
    await waitFor(() => expect(hold.waiting()).toBe(1), GRAPH_WAIT)
    await waitFor(() => expect(more.getAttribute('aria-disabled')).toBe('true'), GRAPH_WAIT)
    expect(more.hasAttribute('disabled')).toBe(false)
    expect(later).toBe(1)
    // A call would only return the running fetch (`cancelRefetch: false`), sending nothing, so the
    // calls, not the requests, show that the handler returns at once.
    expect(fetchNextPage).toHaveBeenCalledTimes(1)
    await user.click(more)
    await pause(100)
    expect(fetchNextPage).toHaveBeenCalledTimes(1)
    expect(later).toBe(1)
    hold.release()
    await within(region).findByText('2 of 2 results shown.', {}, GRAPH_WAIT)
    expect(later).toBe(1)
    expect(requests()).toEqual([pageRequest(901), evidenceRequest(901), evidenceRequest(901, '?after=evidence:951')])
  }, 20_000)

  it("EL-12: the raw toggle's name, its collapsed and expanded states in API order, and the rows with no raw columns or record", async () => {
    const user = userEvent.setup()
    openPanel('relationship:9', {})
    let region = screen.getByRole('region')
    await within(region).findByText('2 of 2 results shown.', {}, GRAPH_WAIT)
    const toggle = within(region).getByRole('button', { name: "Source record's columns: syn-03, line 4 (evidence:21)" })
    expect(toggle.textContent).toBe("Source record's columns")
    expect(toggle.getAttribute('aria-expanded')).toBe('false')
    const controls = toggle.getAttribute('aria-controls') ?? ''
    expect(controls).not.toBe('')
    expect(document.getElementById(controls)).toBeNull()
    const raw = recorded<GraphEvidenceResponse>('evidence-mixed').body.evidence.find((row) => row.id === 'evidence:21')?.record?.raw
    if (raw === null || raw === undefined) throw new Error('evidence:21 has no raw columns')
    expect(within(rowOf(region, 'evidence:21')).queryByText('object_id')).toBeNull()
    toggle.focus()
    await user.keyboard('{Enter}')
    expect(toggle.getAttribute('aria-expanded')).toBe('true')
    const expanded = document.getElementById(controls)
    if (expanded === null) throw new Error('the toggle controls no list')
    expect(Array.from(expanded.querySelectorAll('dt')).map((dt) => dt.textContent)).toEqual(Object.keys(raw))
    expect(Object.keys(raw).slice(0, 4)).toEqual(['effect', 'outcome', 'directed', 'object_id'])
    expect(requests()).toEqual([pageRequest(9), evidenceRequest(9)])
    cleanup()

    openFrom('network-truncated', 'relationship:14')
    region = screen.getByRole('region')
    await within(region).findByText('2 of 2 results shown.', {}, GRAPH_WAIT)
    const names = within(region).getAllByRole('button', { name: /^Source record's columns: / }).map((button) => button.getAttribute('aria-label'))
    expect(names).toEqual([
      "Source record's columns: syn-15, line 16 (evidence:33)", "Source record's columns: syn-15, line 16 (evidence:34)",
    ])
    expect(requests().slice(2)).toEqual([pageRequest(14), evidenceRequest(14)])
    cleanup()

    const withoutRaw: GraphEvidenceRow = makeGraphEvidenceRow()
    stubPages([['', page({ evidence: [withoutRaw, rowWithoutRecord] })]], totalOfTwo())
    openPanel('relationship:901', {})
    region = screen.getByRole('region')
    await within(region).findByText('2 of 2 results shown.', {}, GRAPH_WAIT)
    expect(fieldLines(rowOf(region, 'evidence:21'), 'Source record')).toEqual(['syn-03, line 4 (evidence:21)', RAW_UNAVAILABLE])
    const noRecord = within(region).getAllByRole('listitem').filter((item) => fieldLinesOrNull(item, 'Source record')?.[0] === 'no stored source record')
    expect(noRecord).toHaveLength(1)
    expect(fieldLines(noRecord[0], 'Source record')).toEqual(['no stored source record'])
    expect(within(region).queryAllByRole('button', { name: /^Source record's columns: / })).toEqual([])
    expect(requests().slice(4)).toEqual([pageRequest(901), evidenceRequest(901)])
  }, 20_000)

  it('EL-13: a failed first page: Try again sends it again and stays, aria-disabled, while its retry runs; a refused one offers none', async () => {
    const user = userEvent.setup()
    const hold = holdRequests()
    let first = 0
    stubPages([
      ['', async () => {
        first += 1
        if (first === 2) await hold.wait()
        return HttpResponse.json({ detail: 'Too expensive.' }, { status: 503 })
      }],
    ])
    const view = openPanel('relationship:901', {})
    let region = screen.getByRole('region')
    await within(region).findByText(TOO_EXPENSIVE, {}, GRAPH_WAIT)
    expect(within(listStatus(region)).getByText(TOO_EXPENSIVE)).toBeInTheDocument()
    const button = within(region).getByRole('button', { name: TRY_EVIDENCE })
    expect(listStatus(region).compareDocumentPosition(button) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    await user.click(button)
    // The retry is held while the row reads its in-flight state.
    await waitFor(() => expect(hold.waiting()).toBe(1), GRAPH_WAIT)
    expect(first).toBe(2)
    await waitFor(() => expect(button.getAttribute('aria-disabled')).toBe('true'), GRAPH_WAIT)
    expect(within(region).getByRole('button', { name: TRY_EVIDENCE })).toBe(button)
    expect(button.hasAttribute('disabled')).toBe(false)
    expect(document.activeElement).toBe(button)
    expect(within(listStatus(region)).getByText(LOADING_EVIDENCE)).toBeInTheDocument()
    hold.release()
    await waitFor(() => expect(button.getAttribute('aria-disabled')).toBeNull(), GRAPH_WAIT)
    await within(listStatus(region)).findByText(TOO_EXPENSIVE, {}, GRAPH_WAIT)
    expect(button.isConnected).toBe(true)
    expect(document.activeElement).toBe(button)
    expect(requests()).toEqual([pageRequest(901), evidenceRequest(901), evidenceRequest(901)])

    // A first page refused with a kind a repeat cannot change offers no Try again.
    view.unmount()
    server.resetHandlers()
    const from = log.urls.length
    let refused = 0
    stubPages([
      ['', () => {
        refused += 1
        return HttpResponse.json({ detail: 'Bad request.', errors: [] }, { status: 400 })
      }],
    ])
    openPanel('relationship:901', {})
    region = screen.getByRole('region')
    // The list mounts with the body, after the debounce.
    await within(region).findByText(REFUSED, {}, GRAPH_WAIT)
    expect(within(listStatus(region)).getByText(REFUSED)).toBeInTheDocument()
    expect(within(region).queryByRole('button', { name: TRY_EVIDENCE })).toBeNull()
    await pause(300)
    expect(refused).toBe(1)
    expect(requests().slice(from)).toEqual([pageRequest(901), evidenceRequest(901)])
  }, 20_000)

  it("EL-14: the list's sub-heading and the words that say it shows every outcome", async () => {
    openPanel('relationship:9', {})
    const region = screen.getByRole('region')
    await within(region).findByText('2 of 2 results shown.', {}, GRAPH_WAIT)
    expect(within(region).getByRole('heading', { level: 3, name: 'Evidence (every outcome)' })).toBeInTheDocument()
    expect(within(region).getByText('The list shows every outcome, so the results against this relationship are read beside the results for it.')).toBeInTheDocument()
    expect(requests()).toEqual([pageRequest(9), evidenceRequest(9)])
  }, 20_000)

  it('EL-15: relationship:14 from network-truncated: one record, two results, each with its own reporting paper', async () => {
    openFrom('network-truncated', 'relationship:14')
    const region = screen.getByRole('region')
    await within(region).findByText('2 of 2 results shown.', {}, GRAPH_WAIT)
    await waitFor(() => expect(within(region).getAllByRole('status')[0].textContent).toBe(''), GRAPH_WAIT)
    const first = rowOf(region, 'evidence:33')
    const second = rowOf(region, 'evidence:34')
    expect(fieldLines(first, 'Source record')).toEqual(['syn-15, line 16 (evidence:33)'])
    expect(fieldLines(second, 'Source record')).toEqual(['syn-15, line 16 (evidence:34)'])
    expect(fieldLines(first, 'Publications')[0]).toBe('reported in: SYN demonstration paper 1 (synthetic: not a real publication) (synthetic)')
    expect(fieldLines(second, 'Publications')[0]).toBe('reported in: SYN demonstration paper 2 (synthetic: not a real publication) (synthetic)')
    const headline = Array.from(region.querySelectorAll('p')).filter((paragraph) => (paragraph.textContent ?? '').startsWith('Supporting results (assertions): '))
    expect(headline.map((paragraph) => paragraph.textContent)).toEqual(['Supporting results (assertions): 2Independent evidence: 2'])
    expect(within(region).getByText('Publications reporting the supporting results: 2')).toBeInTheDocument()
    expect(requests()).toEqual([pageRequest(14), evidenceRequest(14)])
  }, 20_000)

  it("EL-16: after the last page, focus is on the count line, and no paging button remains", async () => {
    const user = userEvent.setup()
    openPanel('relationship:9', { outcome: 'all' }, { pageSize: 1 })
    const region = screen.getByRole('region')
    await within(region).findByText('1 of 2 results shown.', {}, GRAPH_WAIT)
    within(region).getByRole('button', { name: SHOW_MORE }).focus()
    await user.keyboard('{Enter}')
    await within(listStatus(region)).findByText('2 of 2 results shown.', {}, GRAPH_WAIT)
    await waitFor(() => expect(document.activeElement?.textContent).toBe('2 of 2 results shown.'), GRAPH_WAIT)
    expect(document.activeElement?.tagName).toBe('P')
    expect(document.activeElement?.getAttribute('tabindex')).toBe('-1')
    expect(within(region).queryByRole('button', { name: SHOW_MORE })).toBeNull()
    expect(requests()).toEqual([pageRequest(9, '?outcome=all'), evidenceRequest(9, '?page_size=1'), evidenceRequest(9, '?after=evidence:21&page_size=1')])
  }, 20_000)

  it('EL-17: a row that is null passes the page guard and throws, and the boundary keeps the rest of the page', async () => {
    stubPages([['', () => HttpResponse.json({ evidence: [null], page: { page_size: 50, next: null } })]])
    render(
      <>
        <RelationshipPanel relationshipRef="relationship:901" params={{}} vocabularies={vocabularies()} onClose={() => {}} />
        <p>The rest of the page</p>
      </>,
      { wrapper: graphWrapper(newGraphClient()) },
    )
    const region = screen.getByRole('region')
    const caught = await within(region).findByText(BAD_BODY, {}, GRAPH_WAIT)
    expect(caught.closest('[role="status"]')).toBeNull()
    expect(within(region).queryAllByRole('status')).toEqual([])
    expect(within(region).getByRole('heading', { level: 2 })).toBeInTheDocument()
    expect(within(region).getByRole('button', { name: 'Close' })).toBeInTheDocument()
    expect(screen.getByText('The rest of the page')).toBeInTheDocument()
    await pause(100)
    takeCaughtLog()
    expect(requests()).toEqual([pageRequest(901), evidenceRequest(901)])
  }, 20_000)

  it('EL-18: a repeated cursor is not followed: one row, no paging button, and the bad-body words', async () => {
    const user = userEvent.setup()
    const repeat = page({ evidence: [regulationRowWithMethods], page: { page_size: 50, next: 'evidence:951' } })
    stubPages([['', repeat], ['evidence:951', repeat]])
    openPanel('relationship:901', {})
    const region = screen.getByRole('region')
    await within(region).findByText('1 of 1 result shown.', {}, GRAPH_WAIT)
    await user.click(within(region).getByRole('button', { name: SHOW_MORE }))
    await within(listStatus(region)).findByText(BAD_BODY, {}, GRAPH_WAIT)
    expect(within(region).queryByRole('button', { name: SHOW_MORE })).toBeNull()
    expect(within(region).queryByRole('button', { name: TRY_EVIDENCE })).toBeNull()
    expect(within(region).getAllByRole('button', { name: /^Source record's columns: / })).toHaveLength(1)
    await pause(300)
    expect(requests()).toEqual([pageRequest(901), evidenceRequest(901), evidenceRequest(901, '?after=evidence:951')])
  }, 20_000)

  it('EL-19: a later page asked for offline reads as loading, the button keeps focus aria-disabled, and the page goes once online', async () => {
    const user = userEvent.setup()
    openPanel('relationship:9', { outcome: 'all' }, { pageSize: 1 })
    const region = screen.getByRole('region')
    const loaded = [pageRequest(9, '?outcome=all'), evidenceRequest(9, '?page_size=1')]
    await within(region).findByText('1 of 2 results shown.', {}, GRAPH_WAIT)
    const more = within(region).getByRole('button', { name: SHOW_MORE })
    more.focus()
    expect(requests()).toEqual(loaded)
    act(() => {
      onlineManager.setOnline(false)
    })
    await user.keyboard('{Enter}')
    await pause(600)
    expect(requests()).toEqual(loaded)
    expect(Array.from(listStatus(region).children).map((line) => line.textContent)).toEqual(['1 of 2 results shown.', LOADING_EVIDENCE])
    expect(listStatus(region).textContent).not.toContain(LOADING_MORE)
    expect(document.activeElement).toBe(more)
    expect(more.getAttribute('aria-disabled')).toBe('true')
    expect(more.style.cursor).toBe('default')
    await act(async () => {
      onlineManager.setOnline(true)
    })
    await within(listStatus(region)).findByText('2 of 2 results shown.', {}, GRAPH_WAIT)
    await waitFor(() => expect(document.activeElement?.textContent).toBe('2 of 2 results shown.'), GRAPH_WAIT)
    expect(document.activeElement?.tagName).toBe('P')
    expect(within(region).queryByRole('button', { name: SHOW_MORE })).toBeNull()
    expect(within(region).queryByRole('button', { name: TRY_EVIDENCE })).toBeNull()
    expect(requests()).toEqual([...loaded, evidenceRequest(9, '?after=evidence:21&page_size=1')])
  }, 20_000)

  it('EL-20: a failed re-read keeps the rows and words its failure; its Try again stays aria-disabled while it runs, then focus is on the count line', async () => {
    const user = userEvent.setup()
    const hold = holdRequests()
    const client = newGraphClient()
    let calls = 0
    stubPages([['', async () => {
      calls += 1
      if (calls === 2) return HttpResponse.json({ detail: 'Too expensive.' }, { status: 503 })
      if (calls === 3) await hold.wait()
      return page({ evidence: [regulationRowWithMethods] })()
    }]])
    render(
      <RelationshipPanel relationshipRef="relationship:901" params={{}} vocabularies={vocabularies()} onClose={() => {}} />,
      { wrapper: graphWrapper(client) },
    )
    const region = screen.getByRole('region')
    await within(region).findByText('1 of 1 result shown.', {}, GRAPH_WAIT)
    await rereadEvidence(client, 'relationship:901')
    await within(listStatus(region)).findByText(TOO_EXPENSIVE, {}, GRAPH_WAIT)
    expect(Array.from(listStatus(region).children).map((line) => line.textContent)).toEqual(['1 of 1 result shown.', TOO_EXPENSIVE])
    expect(rowOf(region, 'evidence:951')).toBeInTheDocument()
    const button = within(region).getByRole('button', { name: TRY_EVIDENCE })
    expect(listStatus(region).compareDocumentPosition(button) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    await user.click(button)
    // The re-read is held while the row reads its in-flight state.
    await waitFor(() => expect(hold.waiting()).toBe(1), GRAPH_WAIT)
    await waitFor(() => expect(button.getAttribute('aria-disabled')).toBe('true'), GRAPH_WAIT)
    expect(document.activeElement).toBe(button)
    hold.release()
    await waitFor(() => expect(button.isConnected).toBe(false), GRAPH_WAIT)
    await waitFor(() => expect(document.activeElement?.textContent).toBe('1 of 1 result shown.'), GRAPH_WAIT)
    expect(document.activeElement?.tagName).toBe('P')
    expect(requests()).toEqual([pageRequest(901), evidenceRequest(901), evidenceRequest(901), evidenceRequest(901)])
  }, 20_000)

  it('EL-21: a failed first page whose Try again succeeds: the row, no Try again, and focus on the count line', async () => {
    const user = userEvent.setup()
    const hold = holdRequests()
    let first = 0
    stubPages([['', async () => {
      first += 1
      if (first === 1) return HttpResponse.json({ detail: 'Too expensive.' }, { status: 503 })
      await hold.wait()
      return page({ evidence: [regulationRowWithMethods] })()
    }]])
    openPanel('relationship:901', {})
    const region = screen.getByRole('region')
    await within(region).findByText(TOO_EXPENSIVE, {}, GRAPH_WAIT)
    const button = within(region).getByRole('button', { name: TRY_EVIDENCE })
    await user.click(button)
    await waitFor(() => expect(hold.waiting()).toBe(1), GRAPH_WAIT)
    await waitFor(() => expect(button.getAttribute('aria-disabled')).toBe('true'), GRAPH_WAIT)
    expect(within(listStatus(region)).getByText(LOADING_EVIDENCE)).toBeInTheDocument()
    hold.release()
    await within(listStatus(region)).findByText('1 of 1 result shown.', {}, GRAPH_WAIT)
    expect(button.isConnected).toBe(false)
    expect(rowOf(region, 'evidence:951')).toBeInTheDocument()
    await waitFor(() => expect(document.activeElement?.textContent).toBe('1 of 1 result shown.'), GRAPH_WAIT)
    expect(document.activeElement?.tagName).toBe('P')
    expect(document.activeElement?.getAttribute('tabindex')).toBe('-1')
    expect(requests()).toEqual([pageRequest(901), evidenceRequest(901), evidenceRequest(901)])
  }, 20_000)

  it("EL-22: a failed re-read's Try again pressed offline stays aria-disabled with focus, a second press starts nothing, and one request goes once online", async () => {
    const user = userEvent.setup()
    const client = newGraphClient()
    let calls = 0
    stubPages([['', () => {
      calls += 1
      return calls === 2 ? HttpResponse.json({ detail: 'Too expensive.' }, { status: 503 }) : page({ evidence: [regulationRowWithMethods] })()
    }]])
    // Set before the mount, as EL-11's: only a press calls it in this row, since a reconnect leaves a stored 503 alone.
    const refetch = vi.spyOn(QueryObserver.prototype, 'refetch')
    render(
      <RelationshipPanel relationshipRef="relationship:901" params={{}} vocabularies={vocabularies()} onClose={() => {}} />,
      { wrapper: graphWrapper(client) },
    )
    const region = screen.getByRole('region')
    await within(region).findByText('1 of 1 result shown.', {}, GRAPH_WAIT)
    await rereadEvidence(client, 'relationship:901')
    await within(listStatus(region)).findByText(TOO_EXPENSIVE, {}, GRAPH_WAIT)
    const button = within(region).getByRole('button', { name: TRY_EVIDENCE })
    button.focus()
    act(() => {
      onlineManager.setOnline(false)
    })
    await user.keyboard('{Enter}')
    await pause(300)
    // Offline its re-read waits for the connection, which is a running retry: busy, with the failure's words.
    expect(button.getAttribute('aria-disabled')).toBe('true')
    expect(button.style.cursor).toBe('default')
    expect(document.activeElement).toBe(button)
    expect(Array.from(listStatus(region).children).map((line) => line.textContent)).toEqual(['1 of 1 result shown.', TOO_EXPENSIVE])
    expect(refetch).toHaveBeenCalledTimes(1)
    await user.keyboard('{Enter}')
    await pause(300)
    expect(refetch).toHaveBeenCalledTimes(1)
    expect(calls).toBe(2)
    await act(async () => {
      onlineManager.setOnline(true)
    })
    await waitFor(() => expect(button.isConnected).toBe(false), GRAPH_WAIT)
    await waitFor(() => expect(document.activeElement?.textContent).toBe('1 of 1 result shown.'), GRAPH_WAIT)
    expect(calls).toBe(3)
    expect(requests()).toEqual([pageRequest(901), evidenceRequest(901), evidenceRequest(901), evidenceRequest(901)])
  }, 20_000)

  it('EL-23: after a failed re-read that offers no Try again, Show more keeps its words, its place and focus while its own page runs', async () => {
    const user = userEvent.setup()
    const hold = holdRequests()
    const client = newGraphClient()
    render(
      <RelationshipPanel relationshipRef="relationship:9" params={{ outcome: 'all' }} evidencePageSize={1} vocabularies={vocabularies()} onClose={() => {}} />,
      { wrapper: graphWrapper(client) },
    )
    const region = screen.getByRole('region')
    await within(region).findByText('1 of 2 results shown.', {}, GRAPH_WAIT)
    server.use(http.get(`${REL}:ref/evidence`, async ({ request }) => {
      if (new URL(request.url).searchParams.get('after') === null) return HttpResponse.json({ detail: 'Not found.' }, { status: 404 })
      await hold.wait()
      return HttpResponse.json(recorded<GraphEvidenceResponse>('evidence-mixed-page-2').body)
    }))
    await rereadEvidence(client, 'relationship:9')
    await within(listStatus(region)).findByText("This relationship's evidence is not available to you.", {}, GRAPH_WAIT)
    expect(within(region).queryByRole('button', { name: TRY_EVIDENCE })).toBeNull()
    const more = within(region).getByRole('button', { name: SHOW_MORE })
    more.focus()
    await user.keyboard('{Enter}')
    // Its page is held: the re-read's error is still stored, and the later page is not what failed.
    await waitFor(() => expect(hold.waiting()).toBe(1), GRAPH_WAIT)
    await waitFor(() => expect(more.getAttribute('aria-disabled')).toBe('true'), GRAPH_WAIT)
    expect(more.isConnected).toBe(true)
    expect(more.textContent).toBe(SHOW_MORE)
    expect(document.activeElement).toBe(more)
    expect(within(listStatus(region)).getByText(LOADING_MORE)).toBeInTheDocument()
    hold.release()
    await within(listStatus(region)).findByText('2 of 2 results shown.', {}, GRAPH_WAIT)
    await waitFor(() => expect(document.activeElement?.textContent).toBe('2 of 2 results shown.'), GRAPH_WAIT)
    expect(requests()).toEqual([
      pageRequest(9, '?outcome=all'), evidenceRequest(9, '?page_size=1'), evidenceRequest(9, '?page_size=1'),
      evidenceRequest(9, '?after=evidence:21&page_size=1'),
    ])
  }, 20_000)

  it("EL-24: a reconnect's re-read after a failed later page offers no Try again nobody pressed, and the paging control keeps its name until it answers", async () => {
    const user = userEvent.setup()
    const hold = holdRequests()
    openPanel('relationship:9', { outcome: 'all' }, { pageSize: 1 })
    const region = screen.getByRole('region')
    await within(region).findByText('1 of 2 results shown.', {}, GRAPH_WAIT)
    let first = 0
    server.use(http.get(`${REL}:ref/evidence`, async ({ request }) => {
      if (new URL(request.url).searchParams.get('after') !== null) return HttpResponse.json({ detail: 'Request was throttled.' }, { status: 429 })
      first += 1
      await hold.wait()
      return HttpResponse.json(recorded<GraphEvidenceResponse>('evidence-mixed-page-1').body)
    }))
    const control = within(region).getByRole('button', { name: SHOW_MORE })
    control.focus()
    await user.keyboard('{Enter}')
    await within(listStatus(region)).findByText(THROTTLED, {}, GRAPH_WAIT)
    expect(control.textContent).toBe(TRY_EVIDENCE)
    expect(document.activeElement).toBe(control)
    // A failed fetch marks the loaded pages stale, so the reconnect re-reads page 1, held here.
    act(() => {
      onlineManager.setOnline(false)
    })
    await act(async () => {
      onlineManager.setOnline(true)
    })
    await waitFor(() => expect(hold.waiting()).toBe(1), GRAPH_WAIT)
    await pause(50)
    expect(within(region).getAllByRole('button', { name: TRY_EVIDENCE })).toEqual([control])
    expect(document.activeElement).toBe(control)
    expect(Array.from(listStatus(region).children).map((line) => line.textContent)).toEqual(['1 of 2 results shown.', THROTTLED])
    hold.release()
    await waitFor(() => expect(control.textContent).toBe(SHOW_MORE), GRAPH_WAIT)
    expect(document.activeElement).toBe(control)
    expect(Array.from(listStatus(region).children).map((line) => line.textContent)).toEqual(['1 of 2 results shown.'])
    expect(first).toBe(1)
    expect(requests()).toEqual([
      pageRequest(9, '?outcome=all'), evidenceRequest(9, '?page_size=1'), evidenceRequest(9, '?after=evidence:21&page_size=1'),
      evidenceRequest(9, '?page_size=1'),
    ])
  }, 20_000)

  it("EL-25: a reopening, a second panel or a settled outcome change at once after a failed later page re-reads page 1, and each list keeps that page's words and control until it answers", async () => {
    const user = userEvent.setup()
    const panel = (params: GraphRelationshipParams) => (
      <RelationshipPanel relationshipRef="relationship:9" params={params} evidencePageSize={1} vocabularies={vocabularies()} onClose={() => {}} />
    )
    const seen: [string, string[][]][] = []
    for (const [status, detail, words] of [[429, 'Request was throttled.', THROTTLED], [404, 'Not found.', NOT_AVAILABLE]] as const) {
      for (const trigger of ['reopen', 'second', 'outcome'] as const) {
        const hold = holdRequests()
        const client = newGraphClient()
        const start = log.urls.length
        const view = render(panel({ outcome: 'all' }), { wrapper: graphWrapper(client) })
        const region = screen.getByRole('region')
        await within(region).findByText('1 of 2 results shown.', {}, GRAPH_WAIT)
        let first = 0
        server.use(http.get(`${REL}:ref/evidence`, async ({ request }) => {
          if (new URL(request.url).searchParams.get('after') !== null) return HttpResponse.json({ detail }, { status })
          first += 1
          await hold.wait()
          return HttpResponse.json(recorded<GraphEvidenceResponse>('evidence-mixed-page-1').body)
        }))
        within(region).getByRole('button', { name: SHOW_MORE }).focus()
        await user.keyboard('{Enter}')
        await within(listStatus(region)).findByText(words, {}, GRAPH_WAIT)
        expect(listControls(region), `${trigger} ${status}: the mounted list`).toEqual(status === 429 ? [TRY_EVIDENCE] : [])
        // The failure marked the loaded pages stale, so each new observer of the evidence re-reads page 1 at once (8.2), held here.
        if (trigger === 'reopen') {
          view.unmount()
          render(panel({ outcome: 'all' }), { wrapper: graphWrapper(client) })
        } else if (trigger === 'second') {
          view.rerender(<>{panel({ outcome: 'all' })}{panel({ outcome: 'all' })}</>)
        } else {
          // The body is keyed by the relationship's params, while the evidence's drop the outcome.
          view.rerender(panel({}))
        }
        await waitFor(() => expect(hold.waiting()).toBe(1), GRAPH_WAIT)
        const regions = screen.getAllByRole('region')
        expect(regions).toHaveLength(trigger === 'second' ? 2 : 1)
        if (trigger === 'second') expect(regions[0]).toBe(region)
        for (const shown of regions) {
          await waitFor(() => expect(listLines(shown)).toEqual(['1 of 2 results shown.', words]), GRAPH_WAIT)
        }
        seen.push([`${trigger} ${status}`, regions.map((shown) => listControls(shown))])
        hold.release()
        for (const shown of regions) {
          await waitFor(() => expect(listLines(shown)).toEqual(['1 of 2 results shown.']), GRAPH_WAIT)
          expect(listControls(shown), `${trigger} ${status}: once the re-read answers`).toEqual([SHOW_MORE])
        }
        expect(first).toBe(1)
        expect(requests().slice(start)).toEqual([
          pageRequest(9, '?outcome=all'), evidenceRequest(9, '?page_size=1'), evidenceRequest(9, '?after=evidence:21&page_size=1'),
          ...(trigger === 'outcome' ? [pageRequest(9)] : []), evidenceRequest(9, '?page_size=1'),
        ])
        cleanup()
        server.resetHandlers()
      }
    }
    // While the re-read runs, every list reads as the mounted list did after the failure, its control busy while a fetch
    // runs: never a busy "Try again" nobody pressed beside "Show more", nor "Show more" after a refused page.
    const busy = (control: string) => `${control} (aria-disabled)`
    expect(seen).toEqual([
      ['reopen 429', [[busy(TRY_EVIDENCE)]]], ['second 429', [[busy(TRY_EVIDENCE)], [busy(TRY_EVIDENCE)]]], ['outcome 429', [[busy(TRY_EVIDENCE)]]],
      ['reopen 404', [[]]], ['second 404', [[], []]], ['outcome 404', [[]]],
    ])
  }, 20_000)

  it('EL-26: a list mounted while a fetch begun before it runs over a stored failure reads the failure by its request, as the list that began the fetch does', async () => {
    const user = userEvent.setup()
    const panel = (params: GraphRelationshipParams) => (
      <RelationshipPanel relationshipRef="relationship:9" params={params} evidencePageSize={1} vocabularies={vocabularies()} onClose={() => {}} />
    )
    const loaded = [pageRequest(9, '?outcome=all'), evidenceRequest(9, '?page_size=1')]
    const later = evidenceRequest(9, '?after=evidence:21&page_size=1')
    const busy = (control: string) => `${control} (aria-disabled)`
    const seen: [string, string[][]][] = []
    const refusals = [[429, 'Request was throttled.', THROTTLED], [404, 'Not found.', NOT_AVAILABLE]] as const

    // A list that mounts while the re-read after a failed later page already runs joins that fetch, whose start reset its direction.
    for (const [status, detail, words] of refusals) {
      for (const trigger of ['reopen twice', 'second panel'] as const) {
        const hold = holdRequests()
        const client = newGraphClient()
        const start = log.urls.length
        const view = render(panel({ outcome: 'all' }), { wrapper: graphWrapper(client) })
        const region = screen.getByRole('region')
        await within(region).findByText('1 of 2 results shown.', {}, GRAPH_WAIT)
        let first = 0
        server.use(http.get(`${REL}:ref/evidence`, async ({ request }) => {
          if (new URL(request.url).searchParams.get('after') !== null) return HttpResponse.json({ detail }, { status })
          first += 1
          await hold.wait()
          return HttpResponse.json(recorded<GraphEvidenceResponse>('evidence-mixed-page-1').body)
        }))
        within(region).getByRole('button', { name: SHOW_MORE }).focus()
        await user.keyboard('{Enter}')
        await within(listStatus(region)).findByText(words, {}, GRAPH_WAIT)
        const readings: string[][] = []
        if (trigger === 'reopen twice') {
          // The first reopening's mount starts the re-read, held; the panel is closed and reopened again while it runs.
          view.unmount()
          const reopened = render(panel({ outcome: 'all' }), { wrapper: graphWrapper(client) })
          await waitFor(() => expect(hold.waiting()).toBe(1), GRAPH_WAIT)
          await waitFor(() => expect(listLines(screen.getByRole('region'))).toEqual(['1 of 2 results shown.', words]), GRAPH_WAIT)
          readings.push(listControls(screen.getByRole('region')))
          reopened.unmount()
          render(panel({ outcome: 'all' }), { wrapper: graphWrapper(client) })
        } else {
          // A reconnect re-reads page 1 with the panel open, held, and a second panel mounts while it runs.
          act(() => {
            onlineManager.setOnline(false)
          })
          await act(async () => {
            onlineManager.setOnline(true)
          })
          await waitFor(() => expect(hold.waiting()).toBe(1), GRAPH_WAIT)
          view.rerender(<>{panel({ outcome: 'all' })}{panel({ outcome: 'all' })}</>)
        }
        const regions = screen.getAllByRole('region')
        expect(regions).toHaveLength(trigger === 'second panel' ? 2 : 1)
        if (trigger === 'second panel') expect(regions[0]).toBe(region)
        for (const shown of regions) {
          await waitFor(() => expect(listLines(shown)).toEqual(['1 of 2 results shown.', words]), GRAPH_WAIT)
        }
        await pause(50)
        expect(hold.waiting()).toBe(1)
        seen.push([`${trigger} ${status}`, [...readings, ...regions.map((shown) => listControls(shown))]])
        hold.release()
        for (const shown of regions) {
          await waitFor(() => expect(listLines(shown)).toEqual(['1 of 2 results shown.']), GRAPH_WAIT)
          expect(listControls(shown), `${trigger} ${status}: once the re-read answers`).toEqual([SHOW_MORE])
        }
        expect(first).toBe(1)
        expect(requests().slice(start)).toEqual([...loaded, later, evidenceRequest(9, '?page_size=1')])
        cleanup()
        server.resetHandlers()
      }
    }

    // Panel B mounts while A's own page runs: over a refused re-read (R3), and over A's paging "Try again" (R4).
    const pagingCases = [[...refusals[0], 'R3'], [...refusals[1], 'R3'], [...refusals[0], 'R4']] as const
    for (const [status, detail, words, name] of pagingCases) {
      const hold = holdRequests()
      const client = newGraphClient()
      const start = log.urls.length
      const view = render(panel({ outcome: 'all' }), { wrapper: graphWrapper(client) })
      const region = screen.getByRole('region')
      await within(region).findByText('1 of 2 results shown.', {}, GRAPH_WAIT)
      let tries = 0
      server.use(http.get(`${REL}:ref/evidence`, async ({ request }) => {
        const afterPage1 = new URL(request.url).searchParams.get('after') !== null
        if (name === 'R3' && !afterPage1) return HttpResponse.json({ detail }, { status })
        if (!afterPage1) return undefined
        tries += 1
        if (name === 'R4' && tries === 1) return HttpResponse.json({ detail }, { status })
        await hold.wait()
        return HttpResponse.json(recorded<GraphEvidenceResponse>('evidence-mixed-page-2').body)
      }))
      const control = within(region).getByRole('button', { name: SHOW_MORE })
      control.focus()
      if (name === 'R3') await rereadEvidence(client, 'relationship:9')
      else await user.keyboard('{Enter}')
      await within(listStatus(region)).findByText(words, {}, GRAPH_WAIT)
      const stored = listControls(region)
      expect(document.activeElement).toBe(control)
      await user.keyboard('{Enter}')
      await waitFor(() => expect(hold.waiting()).toBe(1), GRAPH_WAIT)
      view.rerender(<>{panel({ outcome: 'all' })}{panel({ outcome: 'all' })}</>)
      const regions = screen.getAllByRole('region')
      expect(regions).toHaveLength(2)
      expect(regions[0]).toBe(region)
      for (const shown of regions) {
        await waitFor(() => expect(listLines(shown)).toEqual(['1 of 2 results shown.', LOADING_MORE]), GRAPH_WAIT)
      }
      await pause(50)
      expect(hold.waiting()).toBe(1)
      seen.push([`${name} ${status}`, [stored, ...regions.map((shown) => listControls(shown))]])
      hold.release()
      for (const shown of regions) {
        await waitFor(() => expect(listLines(shown)).toEqual(['2 of 2 results shown.']), GRAPH_WAIT)
        expect(listControls(shown), `${name} ${status}: once page 2 answers`).toEqual([])
      }
      expect(requests().slice(start)).toEqual(name === 'R3'
        ? [...loaded, evidenceRequest(9, '?page_size=1'), later]
        : [...loaded, later, later])
      cleanup()
      server.resetHandlers()
    }

    // Each list mounted during the fetch reads as the list that began it: a joined re-read keeps the failed page's
    // words and control, never a busy "Try again" nobody pressed beside "Show more", nor "Show more" after a refused
    // page; and a later page keeps "Show more" over a refused re-read, and its own "Try again" over its own failure.
    expect(seen).toEqual([
      ['reopen twice 429', [[busy(TRY_EVIDENCE)], [busy(TRY_EVIDENCE)]]], ['second panel 429', [[busy(TRY_EVIDENCE)], [busy(TRY_EVIDENCE)]]],
      ['reopen twice 404', [[], []]], ['second panel 404', [[], []]],
      ['R3 429', [[TRY_EVIDENCE, SHOW_MORE], [busy(SHOW_MORE)], [busy(SHOW_MORE)]]],
      ['R3 404', [[SHOW_MORE], [busy(SHOW_MORE)], [busy(SHOW_MORE)]]],
      ['R4 429', [[TRY_EVIDENCE], [busy(TRY_EVIDENCE)], [busy(TRY_EVIDENCE)]]],
    ])
  }, 20_000)

  it('EL-27: a failed later page whose error names no request keeps its words and control at rest, through its own retry, and through a re-read its own mount starts or a reconnect sends', async () => {
    const user = userEvent.setup()
    const panel = (params: GraphRelationshipParams) => (
      <RelationshipPanel relationshipRef="relationship:9" params={params} evidencePageSize={1} vocabularies={vocabularies()} onClose={() => {}} />
    )
    const loaded = [pageRequest(9, '?outcome=all'), evidenceRequest(9, '?page_size=1')]
    const later = evidenceRequest(9, '?after=evidence:21&page_size=1')
    const seen: [string, string[][]][] = []
    // A 200 whose body holds no page object is F1's GraphBadBodyError, which names no request: the flags place it, once
    // the query is idle and in the first result of a list whose own mount re-reads page 1.
    for (const trigger of ['own retry', 'reopen', 'second panel', 'outcome', 'reconnect'] as const) {
      const hold = holdRequests()
      const client = newGraphClient()
      const start = log.urls.length
      const view = render(panel({ outcome: 'all' }), { wrapper: graphWrapper(client) })
      const region = screen.getByRole('region')
      await within(region).findByText('1 of 2 results shown.', {}, GRAPH_WAIT)
      let tries = 0
      let first = 0
      server.use(http.get(`${REL}:ref/evidence`, async ({ request }) => {
        if (new URL(request.url).searchParams.get('after') !== null) {
          tries += 1
          if (tries === 2) await hold.wait()
          return HttpResponse.json({ evidence: [], releases: [] })
        }
        first += 1
        await hold.wait()
        return HttpResponse.json(recorded<GraphEvidenceResponse>('evidence-mixed-page-1').body)
      }))
      const control = within(region).getByRole('button', { name: SHOW_MORE })
      control.focus()
      await user.keyboard('{Enter}')
      await within(listStatus(region)).findByText(BAD_BODY, {}, GRAPH_WAIT)
      expect(listLines(region), `${trigger}: at rest`).toEqual(['1 of 2 results shown.', BAD_BODY])
      expect(listControls(region), `${trigger}: at rest`).toEqual([TRY_EVIDENCE])
      expect(document.activeElement).toBe(control)
      if (trigger === 'own retry') {
        // The control sends the failed page's cursor again, held: the same control, busy, keeps focus.
        await user.keyboard('{Enter}')
        await waitFor(() => expect(hold.waiting()).toBe(1), GRAPH_WAIT)
        await waitFor(() => expect(listControls(region)).toEqual([`${TRY_EVIDENCE} (aria-disabled)`]), GRAPH_WAIT)
        expect(listLines(region)).toEqual(['1 of 2 results shown.', LOADING_MORE])
        expect(document.activeElement).toBe(control)
        hold.release()
        await waitFor(() => expect(listLines(region)).toEqual(['1 of 2 results shown.', BAD_BODY]), GRAPH_WAIT)
        expect(listControls(region)).toEqual([TRY_EVIDENCE])
        expect(document.activeElement).toBe(control)
        expect(first).toBe(0)
        expect(requests().slice(start)).toEqual([...loaded, later, later])
      } else {
        // Each new observer re-reads page 1 at once, since graphMayResend allows a bad_body (8.2), and so does a reconnect.
        if (trigger === 'reopen') {
          view.unmount()
          render(panel({ outcome: 'all' }), { wrapper: graphWrapper(client) })
        } else if (trigger === 'second panel') {
          view.rerender(<>{panel({ outcome: 'all' })}{panel({ outcome: 'all' })}</>)
        } else if (trigger === 'outcome') {
          // The body is keyed by the relationship's params, while the evidence's drop the outcome.
          view.rerender(panel({}))
        } else {
          act(() => {
            onlineManager.setOnline(false)
          })
          await act(async () => {
            onlineManager.setOnline(true)
          })
        }
        await waitFor(() => expect(hold.waiting()).toBe(1), GRAPH_WAIT)
        const regions = screen.getAllByRole('region')
        expect(regions).toHaveLength(trigger === 'second panel' ? 2 : 1)
        if (trigger === 'second panel' || trigger === 'reconnect') expect(regions[0]).toBe(region)
        for (const shown of regions) {
          await waitFor(() => expect(listLines(shown)).toEqual(['1 of 2 results shown.', BAD_BODY]), GRAPH_WAIT)
        }
        await pause(50)
        seen.push([trigger, regions.map((shown) => listControls(shown))])
        hold.release()
        for (const shown of regions) {
          await waitFor(() => expect(listLines(shown)).toEqual(['1 of 2 results shown.']), GRAPH_WAIT)
          expect(listControls(shown), `${trigger}: once the re-read answers`).toEqual([SHOW_MORE])
        }
        expect(first).toBe(1)
        expect(requests().slice(start)).toEqual([
          ...loaded, later, ...(trigger === 'outcome' ? [pageRequest(9)] : []), evidenceRequest(9, '?page_size=1'),
        ])
      }
      cleanup()
      server.resetHandlers()
    }
    // While the re-read runs, every list reads as the mounted list did after the failure, its control busy while a fetch
    // runs: never a busy "Try again" nobody pressed beside "Show more".
    const busy = (control: string) => `${control} (aria-disabled)`
    expect(seen).toEqual([
      ['reopen', [[busy(TRY_EVIDENCE)]]], ['second panel', [[busy(TRY_EVIDENCE)], [busy(TRY_EVIDENCE)]]], ['outcome', [[busy(TRY_EVIDENCE)]]],
      ['reconnect', [[busy(TRY_EVIDENCE)]]],
    ])
  }, 20_000)

  it('EL-28: a failed re-read whose error names no request reads as one through each fetch after it, and a later page too long to send leaves no control, also in a list that mounts idle over it', async () => {
    const user = userEvent.setup()
    const panel = () => (
      <RelationshipPanel relationshipRef="relationship:9" params={{ outcome: 'all' }} evidencePageSize={1} vocabularies={vocabularies()} onClose={() => {}} />
    )
    const loaded = [pageRequest(9, '?outcome=all'), evidenceRequest(9, '?page_size=1')]
    const reread = evidenceRequest(9, '?page_size=1')
    const busy = (control: string) => `${control} (aria-disabled)`
    // Page 1's re-read answered with a body the client cannot read, a GraphBadBodyError: the flags place it as a re-read's.
    for (const next of ['Show more', 'Try again', 'reconnect and a second panel'] as const) {
      const hold = holdRequests()
      const client = newGraphClient()
      const start = log.urls.length
      const view = render(panel(), { wrapper: graphWrapper(client) })
      const region = screen.getByRole('region')
      await within(region).findByText('1 of 2 results shown.', {}, GRAPH_WAIT)
      let first = 0
      server.use(http.get(`${REL}:ref/evidence`, async ({ request }) => {
        if (new URL(request.url).searchParams.get('after') !== null) {
          await hold.wait()
          return HttpResponse.json(recorded<GraphEvidenceResponse>('evidence-mixed-page-2').body)
        }
        first += 1
        if (first === 1) return HttpResponse.json({ evidence: [], releases: [] })
        if (next === 'reconnect and a second panel') await hold.wait()
        return HttpResponse.json(recorded<GraphEvidenceResponse>('evidence-mixed-page-1').body)
      }))
      const more = within(region).getByRole('button', { name: SHOW_MORE })
      more.focus()
      await rereadEvidence(client, 'relationship:9')
      await within(listStatus(region)).findByText(BAD_BODY, {}, GRAPH_WAIT)
      expect(listLines(region), `${next}: at rest`).toEqual(['1 of 2 results shown.', BAD_BODY])
      expect(listControls(region), `${next}: at rest`).toEqual([TRY_EVIDENCE, SHOW_MORE])
      expect(document.activeElement).toBe(more)
      if (next === 'Show more') {
        // EL-23's mirror: its own page held, "Show more" keeps its words, its place and focus.
        await user.keyboard('{Enter}')
        await waitFor(() => expect(hold.waiting()).toBe(1), GRAPH_WAIT)
        await waitFor(() => expect(listControls(region)).toEqual([busy(SHOW_MORE)]), GRAPH_WAIT)
        expect(listLines(region)).toEqual(['1 of 2 results shown.', LOADING_MORE])
        expect(document.activeElement).toBe(more)
        hold.release()
        await within(listStatus(region)).findByText('2 of 2 results shown.', {}, GRAPH_WAIT)
        expect(listControls(region)).toEqual([])
        await waitFor(() => expect(document.activeElement?.textContent).toBe('2 of 2 results shown.'), GRAPH_WAIT)
        expect(requests().slice(start)).toEqual([...loaded, reread, evidenceRequest(9, '?after=evidence:21&page_size=1')])
      } else if (next === 'Try again') {
        // Its click re-reads page 1, never the later page.
        await user.click(within(region).getByRole('button', { name: TRY_EVIDENCE }))
        await waitFor(() => expect(listLines(region)).toEqual(['1 of 2 results shown.']), GRAPH_WAIT)
        expect(listControls(region)).toEqual([SHOW_MORE])
        expect(requests().slice(start)).toEqual([...loaded, reread, reread])
      } else {
        // A reconnect re-reads page 1, held, and a second panel mounts while it runs: each list reads the refetch row.
        act(() => {
          onlineManager.setOnline(false)
        })
        await act(async () => {
          onlineManager.setOnline(true)
        })
        await waitFor(() => expect(hold.waiting()).toBe(1), GRAPH_WAIT)
        view.rerender(<>{panel()}{panel()}</>)
        const regions = screen.getAllByRole('region')
        expect(regions).toHaveLength(2)
        expect(regions[0]).toBe(region)
        for (const shown of regions) {
          await waitFor(() => expect(listLines(shown)).toEqual(['1 of 2 results shown.', BAD_BODY]), GRAPH_WAIT)
        }
        await pause(50)
        expect(regions.map((shown) => listControls(shown))).toEqual([[busy(TRY_EVIDENCE), busy(SHOW_MORE)], [busy(TRY_EVIDENCE), busy(SHOW_MORE)]])
        hold.release()
        for (const shown of regions) {
          await waitFor(() => expect(listLines(shown)).toEqual(['1 of 2 results shown.']), GRAPH_WAIT)
          expect(listControls(shown)).toEqual([SHOW_MORE])
        }
        expect(first).toBe(2)
        expect(requests().slice(start)).toEqual([...loaded, reread, reread])
      }
      cleanup()
      server.resetHandlers()
    }

    // A next too long to send is refused before any request goes, and graphMayResend refuses a too_long, so a
    // reopening sends nothing and its list mounts idle over the failure.
    const client = newGraphClient()
    const start = log.urls.length
    stubPages([['', pageOf([regulationRowWithMethods], `evidence:${'9'.repeat(5000)}`)]], totalOfTwo())
    const view = render(panel901(), { wrapper: graphWrapper(client) })
    const region = screen.getByRole('region')
    await within(region).findByText('1 of 2 results shown.', {}, GRAPH_WAIT)
    within(region).getByRole('button', { name: SHOW_MORE }).focus()
    await user.keyboard('{Enter}')
    await within(listStatus(region)).findByText(TOO_LONG, {}, GRAPH_WAIT)
    expect(listLines(region)).toEqual(['1 of 2 results shown.', TOO_LONG])
    expect(listControls(region)).toEqual([])
    await waitFor(() => expect(document.activeElement?.textContent).toBe('1 of 2 results shown.'), GRAPH_WAIT)
    view.unmount()
    render(panel901(), { wrapper: graphWrapper(client) })
    const reopened = screen.getByRole('region')
    await waitFor(() => expect(listLines(reopened)).toEqual(['1 of 2 results shown.', TOO_LONG]), GRAPH_WAIT)
    await pause(400)
    expect(listControls(reopened)).toEqual([])
    expect(requests().slice(start)).toEqual([pageRequest(901), evidenceRequest(901)])
  }, 20_000)

  it('EL-29: at rest the flags place a failure, as the list before the cure did: a re-read refused where it sent the last loaded next, and a later page whose cursor is not a string', async () => {
    const user = userEvent.setup()
    const A = regulationRowWithMethods
    const B = textMiningRow
    const C = makeGraphEvidenceRow()
    const seen: [string, string[], string[]][] = []
    const focused: [string, string | null][] = []
    const read = (name: string, region: HTMLElement) => {
      seen.push([name, listLines(region), listControls(region)])
    }

    // L2: a repeated cursor, then a re-read refused on its second request, which sends the loaded last next again.
    let client = newGraphClient()
    let start = log.urls.length
    stubPages([['', pageOf([A], A.id)], [A.id, pageOf([A], A.id)]], totalOfTwo())
    render(panel901(), { wrapper: graphWrapper(client) })
    let region = screen.getByRole('region')
    await within(region).findByText('1 of 2 results shown.', {}, GRAPH_WAIT)
    await user.click(within(region).getByRole('button', { name: SHOW_MORE }))
    await within(listStatus(region)).findByText(BAD_BODY, {}, GRAPH_WAIT)
    stubPages([['', pageOf([A], A.id)], [A.id, refusal(429)]], totalOfTwo())
    await rereadEvidence(client, 'relationship:901')
    await evidenceSettled(client, 'relationship:901', 'error')
    read('L2 at rest', region)
    expect(requests().slice(start)).toEqual([
      pageRequest(901), evidenceRequest(901), evidenceRequest(901, '?after=evidence:951'),
      evidenceRequest(901), evidenceRequest(901, '?after=evidence:951'),
    ])
    cleanup()
    server.resetHandlers()

    // A later page whose cursor is not a string, which only a broken or hostile server sends: the flags place its
    // refusal at rest, and in the list a reopening mounts, whose own re-read keeps the failed fetch's direction.
    for (const status of [429, 404] as const) {
      client = newGraphClient()
      start = log.urls.length
      const hold = holdRequests()
      let reopened = false
      stubPages([
        ['', async () => {
          if (reopened) await hold.wait()
          return HttpResponse.json({ ...makeEvidenceResponse({ evidence: [A] }), page: { page_size: 50, next: 5 } })
        }],
        ['5', refusal(status)],
      ], totalOfTwo())
      const view = render(panel901(), { wrapper: graphWrapper(client) })
      region = screen.getByRole('region')
      await within(region).findByText('1 of 2 results shown.', {}, GRAPH_WAIT)
      within(region).getByRole('button', { name: SHOW_MORE }).focus()
      await user.keyboard('{Enter}')
      await evidenceSettled(client, 'relationship:901', 'error')
      read(`cursor 5 ${status} at rest`, region)
      focused.push([`cursor 5 ${status} at rest`, document.activeElement?.textContent ?? null])
      reopened = true
      view.unmount()
      render(panel901(), { wrapper: graphWrapper(client) })
      await waitFor(() => expect(hold.waiting()).toBe(1), GRAPH_WAIT)
      await pause(50)
      read(`cursor 5 ${status} reopened`, screen.getByRole('region'))
      hold.release()
      await evidenceSettled(client, 'relationship:901', 'success')
      expect(listControls(screen.getByRole('region'))).toEqual([SHOW_MORE])
      expect(requests().slice(start)).toEqual([pageRequest(901), evidenceRequest(901), evidenceRequest(901, '?after=5'), evidenceRequest(901)])
      cleanup()
      server.resetHandlers()
    }

    // L1: a reopening after GRAPH_DATA_STALE_MS re-reads both loaded pages; page 1's row is gone, so its second request
    // sends the loaded last page's next, and is refused.
    client = newGraphClient()
    start = log.urls.length
    stubPages([['', pageOf([A], A.id)], [A.id, pageOf([B], B.id)], [B.id, pageOf([C], null)]], totalOf(4))
    const opened = render(panel901(), { wrapper: graphWrapper(client) })
    region = screen.getByRole('region')
    await within(region).findByText('1 of 4 results shown.', {}, GRAPH_WAIT)
    await user.click(within(region).getByRole('button', { name: SHOW_MORE }))
    await within(region).findByText('2 of 4 results shown.', {}, GRAPH_WAIT)
    opened.unmount()
    const realNow = Date.now.bind(Date)
    const now = vi.spyOn(Date, 'now').mockImplementation(() => realNow() + 61_000)
    const hold = holdRequests()
    let rereads = 0
    stubPages([
      ['', async () => {
        rereads += 1
        if (rereads === 2) await hold.wait()
        return pageOf([B], B.id)()
      }],
      [B.id, () => (rereads === 1 ? refusal(429)() : pageOf([C], null)())],
    ], totalOf(4))
    render(panel901(), { wrapper: graphWrapper(client) })
    region = screen.getByRole('region')
    await evidenceSettled(client, 'relationship:901', 'error')
    read('L1 at rest', region)

    // The flags name the fetch that stored the error once the query is idle, where the request cannot.
    expect(seen).toEqual([
      ['L2 at rest', ['1 of 2 results shown.', THROTTLED], [TRY_EVIDENCE]],
      ['cursor 5 429 at rest', ['1 of 2 results shown.', THROTTLED], [TRY_EVIDENCE]],
      ['cursor 5 429 reopened', ['1 of 2 results shown.', THROTTLED], [`${TRY_EVIDENCE} (aria-disabled)`]],
      ['cursor 5 404 at rest', ['1 of 2 results shown.', NOT_AVAILABLE], []],
      ['cursor 5 404 reopened', ['1 of 2 results shown.', NOT_AVAILABLE], []],
      ['L1 at rest', ['2 of 4 results shown.', THROTTLED], [TRY_EVIDENCE, SHOW_MORE]],
    ])
    expect(focused).toEqual([['cursor 5 429 at rest', TRY_EVIDENCE], ['cursor 5 404 at rest', '1 of 2 results shown.']])

    // L1's "Try again" pressed: the list saw its re-read start, so the refetch row stays, busy, with focus, until it answers,
    // and "Show more" is busy beside it while that re-read runs.
    const retry = within(region).getByRole('button', { name: TRY_EVIDENCE })
    retry.focus()
    await user.keyboard('{Enter}')
    await waitFor(() => expect(hold.waiting()).toBe(1), GRAPH_WAIT)
    await waitFor(() => expect(listControls(region)).toEqual([`${TRY_EVIDENCE} (aria-disabled)`, `${SHOW_MORE} (aria-disabled)`]), GRAPH_WAIT)
    expect(listLines(region)).toEqual(['2 of 4 results shown.', THROTTLED])
    expect(document.activeElement).toBe(retry)
    hold.release()
    await evidenceSettled(client, 'relationship:901', 'success')
    expect(listLines(region)).toEqual(['2 of 4 results shown.'])
    expect(listControls(region)).toEqual([])
    await waitFor(() => expect(document.activeElement?.textContent).toBe('2 of 4 results shown.'), GRAPH_WAIT)
    expect(rowOf(region, 'evidence:955')).toBeInTheDocument()
    expect(within(region).queryAllByRole('listitem').filter((item) => (item.textContent ?? '').includes('(evidence:951)'))).toEqual([])
    now.mockRestore()
    expect(requests().slice(start)).toEqual([
      pageRequest(901), evidenceRequest(901), evidenceRequest(901, '?after=evidence:951'),
      pageRequest(901), evidenceRequest(901), evidenceRequest(901, '?after=evidence:955'),
      evidenceRequest(901), evidenceRequest(901, '?after=evidence:955'),
    ])
  }, 20_000)

  it('EL-30: a list mounted during a fetch places the stored failure by its request: over three loaded pages by the last one, and after a dropped connection', async () => {
    const user = userEvent.setup()
    const A = regulationRowWithMethods
    const B = textMiningRow
    const C = makeGraphEvidenceRow()
    const D = makeGraphEvidenceRow({ id: 'evidence:996' })
    const busy = (control: string) => `${control} (aria-disabled)`
    const loaded = [pageRequest(901), evidenceRequest(901), evidenceRequest(901, '?after=evidence:951'), evidenceRequest(901, '?after=evidence:955')]
    const seen: [string, string[][]][] = []
    for (const failed of ['later page', 're-read'] as const) {
      const hold = holdRequests()
      const client = newGraphClient()
      const start = log.urls.length
      let holding = false
      let refusing = false
      stubPages([
        ['', async () => {
          if (holding) await hold.wait()
          return pageOf([A], A.id)()
        }],
        [A.id, () => (refusing ? refusal(429)() : pageOf([B], B.id)())],
        [B.id, pageOf([C], C.id)],
        [C.id, async () => {
          if (failed === 'later page') return refusal(429)()
          await hold.wait()
          return pageOf([D], null)()
        }],
      ], totalOf(4))
      const view = render(panel901(), { wrapper: graphWrapper(client) })
      const region = screen.getByRole('region')
      await within(region).findByText('1 of 4 results shown.', {}, GRAPH_WAIT)
      within(region).getByRole('button', { name: SHOW_MORE }).focus()
      await user.keyboard('{Enter}')
      await within(region).findByText('2 of 4 results shown.', {}, GRAPH_WAIT)
      await user.keyboard('{Enter}')
      await within(region).findByText('3 of 4 results shown.', {}, GRAPH_WAIT)
      if (failed === 'later page') {
        // The fourth page, sent with the last loaded page's next, is refused.
        await user.keyboard('{Enter}')
      } else {
        // The re-read of the three is refused on its second request, sent with the first page's next.
        refusing = true
        await rereadEvidence(client, 'relationship:901')
      }
      await evidenceSettled(client, 'relationship:901', 'error')
      const rest = listControls(region)
      if (failed === 'later page') {
        // A reconnect re-reads the three, held on its first request.
        holding = true
        act(() => {
          onlineManager.setOnline(false)
        })
        await act(async () => {
          onlineManager.setOnline(true)
        })
      } else {
        // Show more, its page held.
        await user.keyboard('{Enter}')
      }
      await waitFor(() => expect(hold.waiting()).toBe(1), GRAPH_WAIT)
      view.rerender(<>{panel901()}{panel901()}</>)
      const regions = screen.getAllByRole('region')
      expect(regions).toHaveLength(2)
      expect(regions[0]).toBe(region)
      const during = failed === 'later page' ? ['3 of 4 results shown.', THROTTLED] : ['3 of 4 results shown.', LOADING_MORE]
      for (const shown of regions) {
        await waitFor(() => expect(listLines(shown)).toEqual(during), GRAPH_WAIT)
      }
      await pause(50)
      seen.push([failed, [rest, ...regions.map((shown) => listControls(shown))]])
      hold.release()
      await evidenceSettled(client, 'relationship:901', 'success')
      for (const shown of regions) {
        expect(listLines(shown)).toEqual(failed === 'later page' ? ['3 of 4 results shown.'] : ['4 of 4 results shown.'])
        expect(listControls(shown)).toEqual(failed === 'later page' ? [SHOW_MORE] : [])
      }
      expect(requests().slice(start)).toEqual(failed === 'later page'
        ? [
          ...loaded, evidenceRequest(901, '?after=evidence:21'),
          evidenceRequest(901), evidenceRequest(901, '?after=evidence:951'), evidenceRequest(901, '?after=evidence:955'),
        ]
        : [...loaded, evidenceRequest(901), evidenceRequest(901, '?after=evidence:951'), evidenceRequest(901, '?after=evidence:21')])
      cleanup()
      server.resetHandlers()
    }

    // A dropped connection is axios's error too, holding the request's config and no response; F1 sends it once more.
    const panel9 = () => (
      <RelationshipPanel relationshipRef="relationship:9" params={{ outcome: 'all' }} evidencePageSize={1} vocabularies={vocabularies()} onClose={() => {}} />
    )
    const later = evidenceRequest(9, '?after=evidence:21&page_size=1')
    for (const trigger of ['reopen twice', 'second panel'] as const) {
      const hold = holdRequests()
      const client = newGraphClient()
      const start = log.urls.length
      const view = render(panel9(), { wrapper: graphWrapper(client) })
      const region = screen.getByRole('region')
      await within(region).findByText('1 of 2 results shown.', {}, GRAPH_WAIT)
      let first = 0
      server.use(http.get(`${REL}:ref/evidence`, async ({ request }) => {
        if (new URL(request.url).searchParams.get('after') !== null) return HttpResponse.error()
        first += 1
        await hold.wait()
        return HttpResponse.json(recorded<GraphEvidenceResponse>('evidence-mixed-page-1').body)
      }))
      within(region).getByRole('button', { name: SHOW_MORE }).focus()
      await user.keyboard('{Enter}')
      await within(listStatus(region)).findByText(UNREACHABLE, {}, GRAPH_WAIT)
      const readings = [listControls(region)]
      if (trigger === 'reopen twice') {
        // The first reopening's mount starts the re-read, held; the panel is closed and reopened again while it runs.
        view.unmount()
        const reopened = render(panel9(), { wrapper: graphWrapper(client) })
        await waitFor(() => expect(hold.waiting()).toBe(1), GRAPH_WAIT)
        await waitFor(() => expect(listLines(screen.getByRole('region'))).toEqual(['1 of 2 results shown.', UNREACHABLE]), GRAPH_WAIT)
        readings.push(listControls(screen.getByRole('region')))
        reopened.unmount()
        render(panel9(), { wrapper: graphWrapper(client) })
      } else {
        // A reconnect re-reads page 1 with the panel open, held, and a second panel mounts while it runs.
        act(() => {
          onlineManager.setOnline(false)
        })
        await act(async () => {
          onlineManager.setOnline(true)
        })
        await waitFor(() => expect(hold.waiting()).toBe(1), GRAPH_WAIT)
        view.rerender(<>{panel9()}{panel9()}</>)
      }
      const regions = screen.getAllByRole('region')
      expect(regions).toHaveLength(trigger === 'second panel' ? 2 : 1)
      for (const shown of regions) {
        await waitFor(() => expect(listLines(shown)).toEqual(['1 of 2 results shown.', UNREACHABLE]), GRAPH_WAIT)
      }
      await pause(50)
      seen.push([`dropped connection, ${trigger}`, [...readings, ...regions.map((shown) => listControls(shown))]])
      hold.release()
      for (const shown of regions) {
        await waitFor(() => expect(listLines(shown)).toEqual(['1 of 2 results shown.']), GRAPH_WAIT)
        expect(listControls(shown)).toEqual([SHOW_MORE])
      }
      expect(first).toBe(1)
      expect(requests().slice(start)).toEqual([
        pageRequest(9, '?outcome=all'), evidenceRequest(9, '?page_size=1'), later, later, evidenceRequest(9, '?page_size=1'),
      ])
      cleanup()
      server.resetHandlers()
    }
    // The failure answered a request sent with the last loaded page's next, or with the first page's, or a dropped
    // request for the next page: each list mounted during the next fetch reads it so, as the list that saw it settle does.
    expect(seen).toEqual([
      ['later page', [[TRY_EVIDENCE], [busy(TRY_EVIDENCE)], [busy(TRY_EVIDENCE)]]],
      ['re-read', [[TRY_EVIDENCE, SHOW_MORE], [busy(SHOW_MORE)], [busy(SHOW_MORE)]]],
      ['dropped connection, reopen twice', [[TRY_EVIDENCE], [busy(TRY_EVIDENCE)], [busy(TRY_EVIDENCE)]]],
      ['dropped connection, second panel', [[TRY_EVIDENCE], [busy(TRY_EVIDENCE)], [busy(TRY_EVIDENCE)]]],
    ])
  }, 20_000)

  it('EL-31: the paging control is busy through every fetch of the list, since a press during a re-read of the loaded pages would be lost', async () => {
    const user = userEvent.setup()
    const A = regulationRowWithMethods
    const B = textMiningRow
    const C = makeGraphEvidenceRow()
    const busy = (control: string) => `${control} (aria-disabled)`
    // Before the mount: the observer binds the method in its constructor, and the result hands out that copy.
    const fetchNextPage = vi.spyOn(InfiniteQueryObserver.prototype, 'fetchNextPage')
    const reconnect = async () => {
      act(() => {
        onlineManager.setOnline(false)
      })
      await act(async () => {
        onlineManager.setOnline(true)
      })
    }

    // Two pages loaded and a third to come; a reconnect once stale re-reads both, its first evidence request held.
    const hold = holdRequests()
    let rereading = false
    stubPages([
      ['', async () => {
        if (rereading) await hold.wait()
        return pageOf([A], A.id)()
      }],
      [A.id, pageOf([B], B.id)],
      [B.id, pageOf([C], null)],
    ], totalOf(3))
    render(panel901(), { wrapper: graphWrapper(newGraphClient()) })
    let region = screen.getByRole('region')
    await within(region).findByText('1 of 3 results shown.', {}, GRAPH_WAIT)
    const more = within(region).getByRole('button', { name: SHOW_MORE })
    more.focus()
    await user.keyboard('{Enter}')
    await within(region).findByText('2 of 3 results shown.', {}, GRAPH_WAIT)
    expect(document.activeElement).toBe(more)
    expect(fetchNextPage).toHaveBeenCalledTimes(1)
    rereading = true
    const realNow = Date.now.bind(Date)
    const now = vi.spyOn(Date, 'now').mockImplementation(() => realNow() + 61_000)
    await reconnect()
    await waitFor(() => expect(hold.waiting()).toBe(1), GRAPH_WAIT)
    await waitFor(() => expect(listControls(region)).toEqual([busy(SHOW_MORE)]), GRAPH_WAIT)
    expect(more.hasAttribute('disabled')).toBe(false)
    expect(more.style.cursor).toBe('default')
    expect(document.activeElement).toBe(more)
    expect(listLines(region)).toEqual(['2 of 3 results shown.'])
    // A press would be handed the running re-read, sending nothing: the handler returns at once instead.
    const sentBefore = log.urls.length
    await user.keyboard('{Enter}')
    await pause(100)
    expect(fetchNextPage).toHaveBeenCalledTimes(1)
    expect(log.urls.length).toBe(sentBefore)
    hold.release()
    await waitFor(() => expect(listControls(region)).toEqual([SHOW_MORE]), GRAPH_WAIT)
    expect(document.activeElement).toBe(more)
    await user.keyboard('{Enter}')
    await within(listStatus(region)).findByText('3 of 3 results shown.', {}, GRAPH_WAIT)
    expect(fetchNextPage).toHaveBeenCalledTimes(2)
    expect(listControls(region)).toEqual([])
    await waitFor(() => expect(document.activeElement?.textContent).toBe('3 of 3 results shown.'), GRAPH_WAIT)
    now.mockRestore()
    expect(requests()).toEqual([
      pageRequest(901), evidenceRequest(901), evidenceRequest(901, '?after=evidence:951'),
      pageRequest(901), evidenceRequest(901), evidenceRequest(901, '?after=evidence:951'),
      evidenceRequest(901, '?after=evidence:955'),
    ])
    cleanup()
    server.resetHandlers()

    // A failed later page whose error offers a retry: its control is busy through the reconnect's re-read too.
    const held = holdRequests()
    const start = log.urls.length
    openPanel('relationship:9', { outcome: 'all' }, { pageSize: 1 })
    region = screen.getByRole('region')
    await within(region).findByText('1 of 2 results shown.', {}, GRAPH_WAIT)
    let first = 0
    server.use(http.get(`${REL}:ref/evidence`, async ({ request }) => {
      if (new URL(request.url).searchParams.get('after') !== null) return HttpResponse.json({ detail: 'Request was throttled.' }, { status: 429 })
      first += 1
      await held.wait()
      return HttpResponse.json(recorded<GraphEvidenceResponse>('evidence-mixed-page-1').body)
    }))
    const control = within(region).getByRole('button', { name: SHOW_MORE })
    control.focus()
    await user.keyboard('{Enter}')
    await within(listStatus(region)).findByText(THROTTLED, {}, GRAPH_WAIT)
    expect(listControls(region)).toEqual([TRY_EVIDENCE])
    expect(document.activeElement).toBe(control)
    const calls = fetchNextPage.mock.calls.length
    await reconnect()
    await waitFor(() => expect(held.waiting()).toBe(1), GRAPH_WAIT)
    await waitFor(() => expect(listControls(region)).toEqual([busy(TRY_EVIDENCE)]), GRAPH_WAIT)
    expect(listLines(region)).toEqual(['1 of 2 results shown.', THROTTLED])
    expect(document.activeElement).toBe(control)
    await user.keyboard('{Enter}')
    await pause(100)
    expect(fetchNextPage).toHaveBeenCalledTimes(calls)
    held.release()
    await waitFor(() => expect(listControls(region)).toEqual([SHOW_MORE]), GRAPH_WAIT)
    expect(document.activeElement).toBe(control)
    expect(listLines(region)).toEqual(['1 of 2 results shown.'])
    expect(first).toBe(1)
    expect(requests().slice(start)).toEqual([
      pageRequest(9, '?outcome=all'), evidenceRequest(9, '?page_size=1'), evidenceRequest(9, '?after=evidence:21&page_size=1'),
      evidenceRequest(9, '?page_size=1'),
    ])
  }, 20_000)
})

function fieldLinesOrNull(row: HTMLElement, term: string): string[] | null {
  const found = Array.from(row.querySelectorAll(':scope > dl > dt')).filter((dt) => dt.textContent === term)
  return found.length === 1 ? fieldLines(row, term) : null
}
