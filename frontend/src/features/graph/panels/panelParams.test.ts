import { AxiosError, AxiosHeaders } from 'axios'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { GraphEvidenceParams, GraphRelationshipParams, GraphSelection } from '../../../api/graph/params'
import { GraphBadBodyError, GraphRequestTooLongError } from '../../../api/graph/errors'
import { graphRelationshipOptions } from '../../../api/graph/relationships'
import {
  geneticRelationshipPage, geneticSignType, hostileEntityPage, hostileRelationshipPage, labelledRelease, levelAssignedPage, makeEvidenceResponse,
  makeGraphEdge, makeGraphRelease, physicalEvidencePage, physicalRelationshipPage, regulationRelationshipPage, unspecifiedPage,
} from '../../../mocks/fixtures/typedGraph/extras'
import { recordedEntries } from '../../../mocks/fixtures/typedGraph/index'
import { recorded } from '../../../test/graphHooks'
import type {
  GraphEdge, GraphEntityResponse, GraphEvidenceResponse, GraphNetworkQuery, GraphNetworkResponse, GraphNode, GraphPublication,
  GraphRelationshipResponse, GraphVocabularies,
} from '../types'
import { graphOfNetwork, keptGraph } from '../network/graph'
import { visibleGraph } from '../network/visibility'
import { edgeDescription } from '../network/words'
import { laterPageRequested } from './evidence'
import { isEntityBody, isEvidencePage, isRelationshipBody } from './guards'
import {
  evidenceParamsOf, graphOfRelationship, graphWithNodes, panelParamsKey, relationshipParamsOf, requestedOf, selectionFromParams,
  selectionOf,
} from './panelParams'
import { isSyntheticShown, mergeReleases } from './releases'
import { resolveSelection, selectionVisibility } from './selection'
import type { PanelSelection } from './selection'
import { shownEdgeOf, syntheticFlags } from './summary'

// React and the libraries report a bad value through the console instead of throwing (16.0).
beforeEach(() => {
  vi.spyOn(console, 'warn')
  vi.spyOn(console, 'error')
})

afterEach(() => {
  const calls = [...vi.mocked(console.warn).mock.calls, ...vi.mocked(console.error).mock.calls]
  vi.restoreAllMocks()
  expect(calls.map((args) => args.map(String).join(' '))).toEqual([])
})

const ALL_OUTCOMES = ['supported', 'tested_absent', 'inconclusive', 'not_significant']
/** The 200s of the network file; `off-not-found` is a 404 body with no echo. */
const NETWORKS = [
  'network-tf1', 'network-tf1-outcome-all', 'network-tf1-v1', 'network-tf1-both-releases', 'network-all-edges', 'network-tf1-browser',
  'network-tf1-visitor', 'network-p1-reference', 'network-tf1-tested-absent', 'network-p2-tested-absent', 'network-truncated',
  'network-p2-inconclusive',
]

const network = (name: string) => recorded<GraphNetworkResponse>(name).body
const page = (name: string) => recorded<GraphRelationshipResponse>(name).body
const vocabularies = () => recorded<GraphVocabularies>('vocabularies').body

/** Every recorded network, so a recording added later is converted too. */
function recordedNetworks(): string[] {
  return recordedEntries.filter((entry) => entry.path === '/api/graph/v1/network' && entry.status === 200).map((entry) => entry.name)
}

function edgeIn(edges: readonly GraphEdge[], id: string): GraphEdge {
  const edge = edges.find((candidate) => candidate.id === id)
  if (edge === undefined) throw new Error(`no ${id}`)
  return edge
}

interface ConverterCase {
  label: string
  echo: GraphNetworkQuery | null | undefined
  params: GraphRelationshipParams
  evidence: GraphEvidenceParams
  selection: GraphSelection
}

/** PP-1's echoes, with what each converts to: the values f3-draft-2 and C-2 `convert` printed. */
function converterCases(): ConverterCase[] {
  const echo = (name: string) => network(name).query
  const tf1 = echo('network-tf1')
  return [
    { label: 'null', echo: null, params: {}, evidence: {}, selection: {} },
    { label: 'undefined', echo: undefined, params: {}, evidence: {}, selection: {} },
    { label: 'network-tf1', echo: tf1, params: {}, evidence: {}, selection: {} },
    { label: 'network-tf1-outcome-all', echo: echo('network-tf1-outcome-all'), params: { outcome: 'all' }, evidence: {}, selection: {} },
    { label: 'network-all-edges', echo: echo('network-all-edges'), params: { outcome: 'all' }, evidence: {}, selection: {} },
    {
      label: 'network-tf1-v1',
      echo: echo('network-tf1-v1'),
      params: { releases: ['release:1'] },
      evidence: { releases: ['release:1'] },
      selection: { releases: ['release:1'] },
    },
    {
      label: 'network-tf1-both-releases',
      echo: echo('network-tf1-both-releases'),
      params: { releases: ['release:1', 'release:2'] },
      evidence: { releases: ['release:1', 'release:2'] },
      selection: { releases: ['release:1', 'release:2'] },
    },
    {
      label: 'network-tf1-browser',
      echo: echo('network-tf1-browser'),
      params: { browser: 'synthetic-regulation' },
      evidence: { browser: 'synthetic-regulation' },
      selection: { browser: 'synthetic-regulation' },
    },
    { label: 'network-p1-reference, its seeds and layers dropped', echo: echo('network-p1-reference'), params: {}, evidence: {}, selection: {} },
    { label: 'network-truncated, its max_edges dropped', echo: echo('network-truncated'), params: {}, evidence: {}, selection: {} },
    {
      label: 'network-tf1-tested-absent',
      echo: echo('network-tf1-tested-absent'),
      params: { outcome: ['tested_absent'] },
      evidence: {},
      selection: {},
    },
    { label: "outcome ['all', 'supported']", echo: { ...tf1, outcome: ['all', 'supported'] }, params: { outcome: 'all' }, evidence: {}, selection: {} },
    {
      label: "an explicit outcome ['supported'], its own key",
      echo: { ...tf1, outcome: ['supported'] },
      params: { outcome: ['supported'] },
      evidence: {},
      selection: {},
    },
    { label: 'context {}', echo: { ...tf1, context: {} }, params: {}, evidence: {}, selection: {} },
    {
      label: 'context with a facet',
      echo: { ...tf1, context: { tissue: 'liver' } },
      params: { context: { tissue: 'liver' } },
      evidence: { context: { tissue: 'liver' } },
      selection: {},
    },
    {
      label: 'browser and releases both set, a 400 if both were sent',
      echo: { ...tf1, browser: 'synthetic-regulation', releases: ['release:1'] },
      params: { browser: 'synthetic-regulation' },
      evidence: { browser: 'synthetic-regulation' },
      selection: { browser: 'synthetic-regulation' },
    },
    {
      label: "an empty browser falls to the releases",
      echo: { ...tf1, browser: '', releases: ['release:1'] },
      params: { releases: ['release:1'] },
      evidence: { releases: ['release:1'] },
      selection: { releases: ['release:1'] },
    },
    {
      label: 'every filter, each list as given',
      echo: {
        ...tf1,
        outcome: ['tested_absent', 'supported'],
        evidence_basis: ['experimental'],
        effect: ['inhibiting'],
        method: ['MI:0018'],
        level: ['direct_interaction'],
        score: ['synthetic:confidence:0.5:1'],
        releases: ['release:2'],
      },
      params: {
        outcome: ['tested_absent', 'supported'],
        evidence_basis: ['experimental'],
        effect: ['inhibiting'],
        method: ['MI:0018'],
        level: ['direct_interaction'],
        score: ['synthetic:confidence:0.5:1'],
        releases: ['release:2'],
      },
      evidence: {
        evidence_basis: ['experimental'],
        effect: ['inhibiting'],
        method: ['MI:0018'],
        level: ['direct_interaction'],
        score: ['synthetic:confidence:0.5:1'],
        releases: ['release:2'],
      },
      selection: { releases: ['release:2'] },
    },
  ]
}

/** PP-5's chains: each relationship page recorded under the query the converter makes of its network's echo. */
const PAIRS: readonly {
  network: string
  page: string
  ref: string
  params: GraphRelationshipParams
  query: [string, string][]
}[] = [
  { network: 'network-p1-reference', page: 'relationship-physical', ref: 'relationship:3', params: {}, query: [] },
  {
    network: 'network-all-edges', page: 'relationship-tested-for-all', ref: 'relationship:4', params: { outcome: 'all' },
    query: [['outcome', 'all']],
  },
  {
    network: 'network-tf1-both-releases', page: 'relationship-effect-both-releases', ref: 'relationship:6',
    params: { releases: ['release:1', 'release:2'] }, query: [['releases', 'release:1,release:2']],
  },
  {
    network: 'network-all-edges', page: 'relationship-mixed-all', ref: 'relationship:9', params: { outcome: 'all' },
    query: [['outcome', 'all']],
  },
  {
    network: 'network-tf1-outcome-all', page: 'relationship-tested-absent-all', ref: 'relationship:7', params: { outcome: 'all' },
    query: [['outcome', 'all']],
  },
  {
    network: 'network-tf1-v1', page: 'relationship-effect-v1', ref: 'relationship:6', params: { releases: ['release:1'] },
    query: [['releases', 'release:1']],
  },
  { network: 'network-truncated', page: 'relationship-two-papers', ref: 'relationship:14', params: {}, query: [] },
]

describe('the converter', () => {
  it('PP-1: relationshipParamsOf keeps each filter by name, never a network-only key, and copies every list', () => {
    for (const { label, echo, params } of converterCases()) {
      expect(relationshipParamsOf(echo), label).toStrictEqual(params)
    }
    // A shared list would let a later edit of the echo change the params a panel holds.
    const echo: GraphNetworkQuery = {
      ...network('network-tf1').query,
      outcome: ['supported'],
      evidence_basis: ['experimental'],
      effect: ['activating'],
      method: ['MI:0018'],
      level: ['physical_association'],
      score: ['synthetic:rank:1:3'],
      context: { tissue: 'liver' },
      releases: ['release:1'],
    }
    const params = relationshipParamsOf(echo)
    expect(params.outcome).not.toBe(echo.outcome)
    expect(params.evidence_basis).not.toBe(echo.evidence_basis)
    expect(params.effect).not.toBe(echo.effect)
    expect(params.method).not.toBe(echo.method)
    expect(params.level).not.toBe(echo.level)
    expect(params.score).not.toBe(echo.score)
    expect(params.context).not.toBe(echo.context)
    expect(params.releases).not.toBe(echo.releases)
    expect(selectionOf(echo).releases).not.toBe(echo.releases)
  })

  it('PP-2: no recorded echo, nor any network-only key, reaches the relationship params', () => {
    const allowed = ['outcome', 'evidence_basis', 'effect', 'method', 'level', 'score', 'context', 'releases', 'browser']
    const names = recordedNetworks()
    expect(names).toEqual(expect.arrayContaining(NETWORKS))
    for (const name of names) {
      const echo = network(name).query
      // Every echo names seeds, which a converter that spreads the echo would send: a 400 on these routes.
      expect(echo.seeds.length, name).toBeGreaterThan(0)
      expect(Object.keys(relationshipParamsOf(echo)).filter((key) => !allowed.includes(key)), name).toEqual([])
    }
    // `expand` stands for a network-only key a later version adds, which no converter knows.
    const everyNetworkKey = {
      ...network('network-tf1').query,
      layers: ['family:physical'],
      direction: 'out' as const,
      taxon: 32630,
      include_partner_edges: true,
      max_edges: 5,
      expand: ['entity:1'],
    }
    expect(relationshipParamsOf(everyNetworkKey)).toStrictEqual({})
  })

  it('PP-3: the evidence params drop outcome and add no page_size; the selection and the requested outcomes', () => {
    for (const { label, echo, evidence, selection } of converterCases()) {
      const converted = evidenceParamsOf(relationshipParamsOf(echo))
      expect(converted, label).toStrictEqual(evidence)
      expect('outcome' in converted, label).toBe(false)
      expect('page_size' in converted, label).toBe(false)
      expect(selectionOf(echo), label).toStrictEqual(selection)
    }
    // The key is deleted from a copy: the panel's own params keep their outcome.
    const params: GraphRelationshipParams = { outcome: 'all', releases: ['release:1'] }
    expect(evidenceParamsOf(params)).toStrictEqual({ releases: ['release:1'] })
    expect(params).toStrictEqual({ outcome: 'all', releases: ['release:1'] })

    expect(requestedOf({})).toEqual(['supported'])
    expect(requestedOf({ outcome: ['supported'] })).toEqual(['supported'])
    expect(requestedOf({ outcome: 'all' })).toEqual(ALL_OUTCOMES)
    expect(requestedOf({ outcome: ['tested_absent', 'supported'] })).toEqual(['supported', 'tested_absent'])

    expect(selectionFromParams({})).toStrictEqual({})
    expect(selectionFromParams({ outcome: 'all', releases: ['release:1'] })).toStrictEqual({ releases: ['release:1'] })
    expect(selectionFromParams({ browser: 'synthetic-regulation' })).toStrictEqual({ browser: 'synthetic-regulation' })
    expect(selectionFromParams({ level: ['direct_interaction'], browser: '' })).toStrictEqual({})
    const releases = ['release:2']
    expect(selectionFromParams({ releases }).releases).not.toBe(releases)
  })

  it('PP-4: panelParamsKey keys as the query key does, and parses to its last element under toEqual', () => {
    expect(panelParamsKey({ releases: ['release:2', 'release:1'] })).toBe(panelParamsKey({ releases: ['release:1', 'release:2'] }))
    expect(panelParamsKey({ releases: ['release:2', 'release:1'] })).toBe('{"releases":["release:1","release:2"]}')
    expect(panelParamsKey({ outcome: 'all', releases: ['release:1'] })).toBe(panelParamsKey({ releases: ['release:1'], outcome: 'all' }))
    expect(panelParamsKey({ level: ['direct_interaction', 'association', 'direct_interaction'] }))
      .toBe(panelParamsKey({ level: ['association', 'direct_interaction'] }))
    const keys = [panelParamsKey({}), panelParamsKey({ outcome: 'all' }), panelParamsKey({ outcome: ['supported'] })]
    expect(keys).toEqual(['{}', '{"outcome":"all"}', '{"outcome":["supported"]}'])
    expect(new Set(keys).size).toBe(3)
    // The query key keeps the undefined keys that the JSON string drops, so only toEqual holds (M45).
    for (const name of recordedNetworks()) {
      const params = relationshipParamsOf(network(name).query)
      const key = graphRelationshipOptions('visitor', 'relationship:4', params).queryKey
      expect(key[key.length - 1], name).toEqual(JSON.parse(panelParamsKey(params)))
    }
  })
})

describe("a relationship page beside its network's edge", () => {
  it("PP-5: each recorded page's edge equals the network's edge under the same query", () => {
    for (const pair of PAIRS) {
      const entry = recorded<GraphRelationshipResponse>(pair.page)
      expect(entry.path, pair.page).toBe(`/api/graph/v1/relationships/${pair.ref}`)
      expect(entry.query, pair.page).toEqual(pair.query)
      expect(relationshipParamsOf(network(pair.network).query), pair.page).toStrictEqual(pair.params)
      expect(entry.body.edge, pair.page).toEqual(edgeIn(network(pair.network).edges, pair.ref))
    }
  })

  it("PP-6: graphOfRelationship gives the network's sentence for the same edge", () => {
    for (const pair of PAIRS) {
      const body = page(pair.page)
      const networkGraph = keptGraph(graphOfNetwork(network(pair.network))).graph
      const expected = edgeDescription(edgeIn(networkGraph.edges, pair.ref), networkGraph, null, vocabularies())
      expect(expected, pair.page).not.toBe('')
      expect(edgeDescription(body.edge, graphOfRelationship(body), null, vocabularies()), pair.page).toBe(expected)
    }
  })

  it("PP-7: the entity pages' reference relationship:1 is relationship-encodes' edge", () => {
    const encodes = page('relationship-encodes').edge
    expect(encodes.id).toBe('relationship:1')
    expect(recorded<GraphEntityResponse>('entity-tf1').body.reference_relationships[0]).toEqual(encodes)
    expect(edgeIn(recorded<GraphEntityResponse>('entity-p1').body.reference_relationships, 'relationship:1')).toEqual(encodes)
  })

  it("PP-8: graphOfRelationship holds the page's arrays; graphWithNodes appends only the nodes the graph lacks, and no entry that is not a node", () => {
    const tested = page('relationship-tested-for-all')
    const graph = graphOfRelationship(tested)
    expect(graph).toStrictEqual({
      nodes: tested.nodes,
      edges: [tested.edge],
      releases: tested.releases,
      requestedOutcomes: ALL_OUTCOMES,
      query: null,
      page: null,
      warnings: [],
    })
    expect(graph.nodes).toBe(tested.nodes)
    expect(graph.releases).toBe(tested.releases)
    expect(graph.edges[0]).toBe(tested.edge)
    expect(graphOfRelationship(page('relationship-physical')).requestedOutcomes).toEqual(['supported'])
    const absent = page('relationship-tested-absent')
    expect(graphOfRelationship({ ...absent, query: { ...absent.query, outcome: ['tested_absent'] } }).requestedOutcomes)
      .toEqual(['tested_absent'])

    const kept = keptGraph(graphOfNetwork(network('network-tf1'))).graph
    expect(kept.nodes.map((node) => node.id)).toEqual(['entity:1', 'entity:4', 'entity:7'])
    expect(graphWithNodes(kept, undefined)).toBe(kept)
    expect(graphWithNodes(kept, kept.nodes)).toBe(kept)
    expect(graphWithNodes(kept, [])).toBe(kept)
    const entityNodes: readonly GraphNode[] = recorded<GraphEntityResponse>('entity-tf1').body.nodes
    expect(entityNodes.map((node) => node.id)).toEqual(['entity:7', 'entity:10'])
    const merged = graphWithNodes(kept, entityNodes)
    expect(merged.nodes.map((node) => node.id)).toEqual(['entity:1', 'entity:4', 'entity:7', 'entity:10'])
    // The kept graph's own record wins: its entity:7 is the seed.
    expect(merged.nodes[2]).toBe(kept.nodes[2])
    expect(merged.nodes[2]).not.toBe(entityNodes[0])
    expect(merged.nodes[3]).toBe(entityNodes[1])
    expect(merged.edges).toBe(kept.edges)
    expect(merged.query).toBe(kept.query)
    expect(kept.nodes.map((node) => node.id)).toEqual(['entity:1', 'entity:4', 'entity:7'])
    // A body passes only its own guard, and the relationship panel reads these nodes above its boundary,
    // so an entry that is not a node with an id is left out rather than read.
    const malformed = [...entityNodes, null, 'entity:11', { label: 'SYN-NO-ID' }] as unknown as readonly GraphNode[]
    expect(graphWithNodes(kept, malformed)).toEqual(merged)
    expect(graphWithNodes(kept, [null] as unknown as readonly GraphNode[])).toBe(kept)
  })
})

describe('the guards, the release merge and the synthetic words', () => {
  it('PP-9: a body that lacks its shape fails its guard', () => {
    const pages = recordedEntries.filter((entry) => entry.status === 200 && /^\/api\/graph\/v1\/relationships\/[^/]+$/.test(entry.path))
    expect(pages.map((entry) => entry.name)).toEqual(expect.arrayContaining([
      'relationship-mixed', 'relationship-tested-absent', 'relationship-effect-v1', 'relationship-physical', 'relationship-tested-for-all',
      'relationship-effect-both-releases', 'relationship-physical-level', 'relationship-tested-absent-all', 'relationship-effect',
      'relationship-two-papers', 'relationship-mixed-all', 'relationship-encodes',
    ]))
    for (const entry of pages) {
      const ref = entry.path.slice('/api/graph/v1/relationships/'.length)
      expect(isRelationshipBody(entry.body, ref), entry.name).toBe(true)
      expect(isRelationshipBody(entry.body, 'relationship:999'), entry.name).toBe(false)
    }
    // An edge on its own, or a body in a list, is not a page.
    const notPages: unknown[] = [
      {}, { edge: null }, { edge: [] }, { edge: 'relationship:9' }, { id: 'relationship:9' }, [{ edge: { id: 'relationship:9' } }], [],
      'text', null,
    ]
    for (const bad of notPages) expect(isRelationshipBody(bad, 'relationship:9'), JSON.stringify(bad)).toBe(false)
    // It passes with no other key, and a builder that reads one then throws for the boundary to catch (6).
    expect(isRelationshipBody({ edge: { id: 'relationship:9' } }, 'relationship:9')).toBe(true)

    expect(isEntityBody(recorded('entity-tf1').body)).toBe(true)
    expect(isEntityBody(recorded('entity-p1').body)).toBe(true)
    for (const bad of [{ entity: null }, { entity: { label: 'x' } }, { entity: { id: 7 } }, { entity: [] }, [], 'text', null]) {
      expect(isEntityBody(bad), JSON.stringify(bad)).toBe(false)
    }

    const evidence = recordedEntries.filter((entry) => entry.status === 200 && entry.path.endsWith('/evidence'))
    expect(evidence.map((entry) => entry.name)).toEqual(expect.arrayContaining([
      'evidence-mixed', 'evidence-scores', 'evidence-physical', 'evidence-tested-for', 'evidence-mixed-page-1', 'evidence-mixed-page-2',
      'evidence-effect-both-releases', 'evidence-effect-both-releases-page-1', 'evidence-effect-both-releases-page-2',
      'evidence-physical-level', 'evidence-tested-absent', 'evidence-effect-v1', 'evidence-effect', 'evidence-two-papers', 'evidence-encodes',
    ]))
    for (const entry of evidence) expect(isEvidencePage(entry.body), entry.name).toBe(true)
    // A page's rows are `evidence`: a guard on v1's `results` would refuse every real page.
    expect(isEvidencePage({ results: [], page: { page_size: 50, next: null } })).toBe(false)
    for (const bad of [{ evidence: {}, page: {} }, { evidence: [], page: null }, { evidence: [], page: [] }, [], 'text', null]) {
      expect(isEvidencePage(bad), JSON.stringify(bad)).toBe(false)
    }
  })

  it('PP-10: mergeReleases lists each id once, in first-seen order, with the later content', () => {
    const v2 = makeGraphRelease({ id: 'release:2', version: 'old' })
    const v1 = makeGraphRelease({ id: 'release:1' })
    const v2Later = makeGraphRelease({ id: 'release:2', version: 'new' })
    const merged = mergeReleases([[v2], [v1], [v2Later]])
    expect(merged).toEqual([v2Later, v1])
    expect(merged[0]).toBe(v2Later)
    expect(merged[1]).toBe(v1)
    expect(mergeReleases([null, undefined, [v1], [], [v2]])).toEqual([v1, v2])
    expect(mergeReleases([])).toEqual([])

    const pageOne = recorded<GraphEvidenceResponse>('evidence-effect-both-releases-page-1').body.releases
    const pageTwo = recorded<GraphEvidenceResponse>('evidence-effect-both-releases-page-2').body.releases
    expect(pageOne.map((release) => release.id)).toEqual(['release:1'])
    expect(pageTwo.map((release) => release.id)).toEqual(['release:2'])
    expect(mergeReleases([pageOne, pageTwo]).map((release) => release.id)).toEqual(['release:1', 'release:2'])
    // In the panel's order, the relationship page's first: a release both name keeps that place and takes the page's later entry.
    const both = page('relationship-effect-both-releases').releases
    const all = mergeReleases([both, pageOne, pageTwo])
    expect(all.map((release) => release.id)).toEqual(['release:1', 'release:2'])
    expect(all[0]).toBe(pageOne[0])
    expect(all[1]).toBe(pageTwo[0])
  })

  it('PP-11: synthetic words never from edge.is_synthetic alone; the shown edge and its flags', () => {
    const tested = page('relationship-tested-absent')
    expect(tested.edge.is_synthetic).toBe(false)
    expect(tested.edge.releases).toEqual([])
    expect(isSyntheticShown({ releases: tested.releases })).toBe(true)
    const paper: GraphPublication = {
      pmid: null, doi: null, pmcid: null, imex_id: null, local_key: 'test-paper', title: 'Test paper', authors: '', journal: '', year: null,
      url: null, is_synthetic: false,
    }
    expect(labelledRelease.is_synthetic).toBe(false)
    expect(isSyntheticShown({ releases: [labelledRelease], publications: [paper] })).toBe(false)
    expect(isSyntheticShown({ publications: [{ ...paper, is_synthetic: true }] })).toBe(true)
    expect(isSyntheticShown({ flags: [true] })).toBe(true)
    expect(isSyntheticShown({ flags: [false], releases: [labelledRelease] })).toBe(false)
    expect(isSyntheticShown({})).toBe(false)

    expect(syntheticFlags(null)).toEqual([])
    expect(syntheticFlags(tested.edge)).toEqual([false])
    expect(syntheticFlags(makeGraphEdge())).toEqual([true])

    const kept = makeGraphEdge({ id: 'relationship:9' })
    const mixed = page('relationship-mixed')
    expect(shownEdgeOf(null, null)).toBeNull()
    expect(shownEdgeOf(null, kept)).toBe(kept)
    expect(shownEdgeOf(mixed, kept)).toBe(mixed.edge)
    expect(shownEdgeOf(mixed, null)).toBe(mixed.edge)
  })

  it("PP-12: four visibility values, and resolveSelection's record", () => {
    expect(selectionVisibility({ kind: 'edge', id: 'relationship:7' }, null)).toBe('unknown')
    const kept = keptGraph(graphOfNetwork(network('network-tf1-outcome-all'))).graph
    const visible = visibleGraph(kept, { showNonSupporting: false })
    expect(selectionVisibility({ kind: 'edge', id: 'relationship:7' }, visible)).toBe('hidden')
    expect(selectionVisibility({ kind: 'node', id: 'entity:2' }, visible)).toBe('hidden')
    expect(selectionVisibility({ kind: 'node', id: 'entity:7' }, visible)).toBe('visible')
    expect(selectionVisibility({ kind: 'edge', id: 'relationship:5' }, visible)).toBe('visible')
    expect(selectionVisibility({ kind: 'edge', id: 'relationship:999' }, visible)).toBe('absent')
    expect(selectionVisibility({ kind: 'node', id: 'entity:999' }, visible)).toBe('absent')
    // Nodes and edges are looked up by kind, so an entity's id never reads as an edge in view.
    expect(selectionVisibility({ kind: 'edge', id: 'entity:7' }, visible)).toBe('absent')
    const everything = visibleGraph(kept, { showNonSupporting: true })
    expect(selectionVisibility({ kind: 'edge', id: 'relationship:7' }, everything)).toBe('visible')
    expect(selectionVisibility({ kind: 'node', id: 'entity:2' }, everything)).toBe('visible')

    const record = edgeIn(kept.edges, 'relationship:7')
    const resolved = resolveSelection({ kind: 'edge', id: 'relationship:7' }, kept)
    expect(resolved).toStrictEqual({ kind: 'edge', id: 'relationship:7', record })
    expect('record' in resolved && resolved.record).toBe(record)
    // An event's record is a snapshot: the kept graph's record replaces it.
    const stale = resolveSelection({ kind: 'edge', id: 'relationship:7', record: makeGraphEdge({ id: 'relationship:7' }) }, kept)
    expect('record' in stale && stale.record).toBe(record)
    const node = kept.nodes.find((candidate) => candidate.id === 'entity:7')
    expect(resolveSelection({ kind: 'node', id: 'entity:7' }, kept)).toStrictEqual({ kind: 'node', id: 'entity:7', record: node })

    // A selection made with params is the other panel's: its record and nodes stay.
    const own = makeGraphEdge({ id: 'relationship:7' })
    const nodes = recorded<GraphEntityResponse>('entity-tf1').body.nodes
    const withParams: PanelSelection = { kind: 'edge', id: 'relationship:7', record: own, params: {}, nodes }
    const kept7 = resolveSelection(withParams, kept)
    expect(kept7).toStrictEqual({ kind: 'edge', id: 'relationship:7', record: own, params: {}, nodes })
    expect('record' in kept7 && kept7.record).toBe(own)
    expect(kept7.nodes).toBe(nodes)

    expect(resolveSelection({ kind: 'node', id: 'entity:999' }, kept)).toStrictEqual({ kind: 'node', id: 'entity:999' })
    expect(resolveSelection({ kind: 'edge', id: 'relationship:7' }, null)).toStrictEqual({ kind: 'edge', id: 'relationship:7' })
  })
})

describe("the hand-written pages keep the API's invariants", () => {
  it("PP-13: each hand-written page joins entities of the types its relationship type allows, so its panel states no impossible biology", () => {
    const types = [...recorded<GraphVocabularies>('vocabularies').body.relationship_types, geneticSignType]
    type Page = [string, GraphEdge, readonly GraphNode[]]
    const pages: Page[] = [
      ['regulationRelationshipPage', regulationRelationshipPage.edge, regulationRelationshipPage.nodes],
      ['physicalRelationshipPage', physicalRelationshipPage.edge, physicalRelationshipPage.nodes],
      ['geneticRelationshipPage', geneticRelationshipPage.edge, geneticRelationshipPage.nodes],
      ['levelAssignedPage', levelAssignedPage.edge, levelAssignedPage.nodes],
      ['unspecifiedPage', unspecifiedPage.edge, unspecifiedPage.nodes],
      ['hostileRelationshipPage', hostileRelationshipPage.edge, hostileRelationshipPage.nodes],
      ...hostileEntityPage.reference_relationships.map((edge): Page => [`hostileEntityPage ${edge.id}`, edge, hostileEntityPage.nodes]),
    ]
    const refused = pages.flatMap(([name, edge, nodes]) => {
      const entry = types.find((candidate) => candidate.slug === edge.type)
      if (entry === undefined) return [`${name}: ${edge.type} is in no registry`]
      const ends: [string, readonly string[]][] = [[edge.source, entry.allowed_subject_types], [edge.target, entry.allowed_object_types]]
      return ends
        .filter(([ref, allowed]) => !allowed.includes(nodes.find((node) => node.id === ref)?.type ?? ''))
        .map(([ref]) => `${name}: ${edge.type} ${ref}`)
    })
    expect(refused).toEqual([])
    // An isoform the source named resolves onto its protein entry.
    const isoforms = physicalEvidencePage.evidence.flatMap((row) => row.participants.filter((participant) => participant.resolution_basis === 'isoform_entry'))
    expect(isoforms.map((participant) => physicalRelationshipPage.nodes.find((node) => node.id === participant.entity)?.type)).toEqual(['protein'])
  })
})

describe("the evidence list's reading of a stored failure", () => {
  it("PP-14: laterPageRequested reads a failure's request, a later page's when its after is the last loaded page's next, and null when the error cannot say", () => {
    const pageWith = (next: string | null) => makeEvidenceResponse({ page: { page_size: 1, next } })
    const loaded = [pageWith('evidence:951'), pageWith('evidence:955'), pageWith('evidence:21')]
    const sentWith = (params?: unknown) => {
      const config = { params, headers: new AxiosHeaders() }
      return new AxiosError('Request failed with status code 429', 'ERR_BAD_REQUEST', config, null, {
        status: 429, statusText: 'Too Many Requests', headers: {}, config, data: { detail: 'Request was throttled.' },
      })
    }
    // A later page is sent with the last loaded page's next; a re-read's later requests with the earlier pages' nexts.
    expect(laterPageRequested(sentWith({ after: 'evidence:21', page_size: 1 }), loaded)).toBe(true)
    expect(laterPageRequested(sentWith({ after: 'evidence:951', page_size: 1 }), loaded)).toBe(false)
    expect(laterPageRequested(sentWith({ after: 'evidence:955', page_size: 1 }), loaded)).toBe(false)
    expect(laterPageRequested(sentWith({ after: undefined, page_size: 1 }), loaded)).toBe(false)
    expect(laterPageRequested(sentWith({ page_size: 1 }), loaded)).toBe(false)
    expect(laterPageRequested(sentWith({ after: 'evidence:21' }), undefined)).toBe(false)
    expect(laterPageRequested(sentWith({ after: 'evidence:21' }), [])).toBe(false)
    // A dropped connection keeps the request's config and has no response.
    const dropped = new AxiosError('Network Error', AxiosError.ERR_NETWORK, { params: { after: 'evidence:21', page_size: 1 }, headers: new AxiosHeaders() })
    expect(dropped.response).toBeUndefined()
    expect(laterPageRequested(dropped, loaded)).toBe(true)
    // A cursor that is not a string, even one equal to the last page's next, and an error that names no request.
    expect(laterPageRequested(sentWith({ after: 5 }), [{ evidence: [], page: { page_size: 1, next: 5 } }])).toBeNull()
    expect(laterPageRequested(sentWith({ after: null }), loaded)).toBeNull()
    expect(laterPageRequested(sentWith({ after: { at: 'evidence:21' } }), loaded)).toBeNull()
    expect(laterPageRequested(sentWith(), loaded)).toBeNull()
    expect(laterPageRequested(sentWith('after=evidence:21'), loaded)).toBeNull()
    expect(laterPageRequested(new GraphBadBodyError(200), loaded)).toBeNull()
    expect(laterPageRequested(new GraphRequestTooLongError(5000, 4094), loaded)).toBeNull()
    expect(laterPageRequested(new Error('Request failed'), loaded)).toBeNull()
  })
})
