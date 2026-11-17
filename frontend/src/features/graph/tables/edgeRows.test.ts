import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  levelAssignedEdge, makeGraphEdge, makeNetworkNode, makeNetworkResponse, mixedSignEdge, negativeSignEdge, ppiNodes,
  presentationAllKeys, roleOrderedEdge, ligandReceptorType, twinEdges, unknownChannelsEdge, unspecifiedEdge,
} from '../../../mocks/fixtures/typedGraph/extras'
import { recordedEntries } from '../../../mocks/fixtures/typedGraph/index'
import { recorded } from '../../../test/graphHooks'
import type { GraphEdge, GraphEdgeEffect, GraphEntityResponse, GraphNetworkNode, GraphNetworkResponse, GraphVocabularies } from '../types'
import { graphOfNetwork, keptGraph } from '../network/graph'
import type { TypedGraph, TypedGraphNode } from '../network/graph'
import { visibleGraph } from '../network/visibility'
import { edgeDescription, edgeLabel } from '../network/words'
import { relationshipHeading, uniqueNames } from '../panels/panelWords'
import { edgeRows, entityRows, nextSort, sortEdgeRows } from './edgeRows'
import type { EdgeRow, EdgeSortKey } from './edgeRows'
import { countCell, directionCell, effectCell, endCell, levelCell, outcomeCell } from './tableWords'

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

const NOT_REQUESTED = 'not among the requested outcomes'
/** The 200s of the network file; `off-not-found` is a 404 body, on which graphOfNetwork would throw. */
const NETWORKS = [
  'network-tf1', 'network-tf1-outcome-all', 'network-tf1-v1', 'network-tf1-both-releases', 'network-all-edges', 'network-tf1-browser',
  'network-tf1-visitor', 'network-p1-reference', 'network-tf1-tested-absent', 'network-p2-tested-absent', 'network-truncated',
  'network-p2-inconclusive',
]

const vocabularies = () => recorded<GraphVocabularies>('vocabularies').body
const network = (name: string): TypedGraph => graphOfNetwork(recorded<GraphNetworkResponse>(name).body)
const ids = (rows: readonly { id: string }[]) => rows.map((row) => row.id)
const refs = (...numbers: number[]) => numbers.map((n) => `relationship:${n}`)

/** Every recorded network, so a recording added later is tabled too. */
function recordedNetworks(): string[] {
  return recordedEntries.filter((entry) => entry.path === '/api/graph/v1/network' && entry.status === 200).map((entry) => entry.name)
}

function edgeIn(graph: TypedGraph, id: string): GraphEdge {
  const edge = graph.edges.find((candidate) => candidate.id === id)
  if (edge === undefined) throw new Error(`no ${id}`)
  return edge
}

function nodeIn(graph: TypedGraph, id: string): TypedGraphNode {
  const node = graph.nodes.find((candidate) => candidate.id === id)
  if (node === undefined) throw new Error(`no ${id}`)
  return node
}

/** A hand-built graph of SYN- nodes and the edges given, answering the default request. */
function handGraph(nodes: GraphNetworkNode[], edges: GraphEdge[]): TypedGraph {
  return graphOfNetwork(makeNetworkResponse({ nodes, edges }))
}

const gene = (id: string, label: string, isSeed = false) =>
  makeNetworkNode({ id, label, is_seed: isSeed, primary_identifier: { namespace: 'synthetic_gene', value: label } })

describe('edgeRows', () => {
  it("ER-1: the canvas's visible edges in its order, for both showNonSupporting values, each with the network's sentence", () => {
    const vocab = vocabularies()
    const names = recordedNetworks()
    expect(names).toEqual(expect.arrayContaining(NETWORKS))
    for (const name of names) {
      const graph = network(name)
      const kept = keptGraph(graph).graph
      for (const showNonSupporting of [true, false]) {
        const label = `${name} ${showNonSupporting}`
        const visible = visibleGraph(kept, { showNonSupporting }).edges
        const rows = edgeRows(graph, { showNonSupporting, vocabularies: vocab })
        expect(ids(rows), label).toEqual(ids(visible))
        rows.forEach((row, rank) => {
          expect(row.rank, label).toBe(rank)
          expect(row.edge, label).toBe(visible[rank])
          // Every recorded sentence is under the clip, so the row carries it whole.
          const sentence = edgeDescription(row.edge, kept, null, vocab)
          expect(sentence.length, label).toBeLessThan(1000)
          expect(row.description, label).toBe(sentence)
        })
      }
    }
    // The two values differ where the request asked for more than support.
    expect(ids(edgeRows(network('network-tf1-outcome-all'), { showNonSupporting: true, vocabularies: vocab }))).toEqual(refs(5, 6, 7, 8))
    expect(ids(edgeRows(network('network-tf1-outcome-all'), { showNonSupporting: false, vocabularies: vocab }))).toEqual(refs(5, 6, 8))
    expect(ids(edgeRows(network('network-p2-tested-absent'), { showNonSupporting: true, vocabularies: vocab }))).toEqual(refs(4))
    expect(ids(edgeRows(network('network-p2-tested-absent'), { showNonSupporting: false, vocabularies: vocab }))).toEqual([])
    expect(edgeRows(null, { showNonSupporting: true, vocabularies: vocab })).toEqual([])

    // A sentence past the clip is cut as clipText cuts it (M36).
    const long = gene('entity:901', `SYN-${'x'.repeat(5000)}`, true)
    const graph = handGraph([long, gene('entity:902', 'SYN-N902')], [makeGraphEdge()])
    const full = edgeDescription(makeGraphEdge(), keptGraph(graph).graph, null, vocab)
    expect(full.length).toBeGreaterThan(1000)
    const [row] = edgeRows(graph, { showNonSupporting: false, vocabularies: vocab })
    expect(row.description).toBe(`${full.slice(0, 1000)}… (${full.length - 1000} more characters not shown)`)
  })

  it("ER-2: sortEdgeRows sorts a copy by each key both ways, ties by id ascending, and null gives the API's rank", () => {
    const rows = Object.freeze(edgeRows(network('network-all-edges'), { showNonSupporting: true, vocabularies: vocabularies() }))
    const rank = refs(3, 4, 9, 14, 5, 6, 7, 8, 10, 11, 12, 13)
    expect(ids(rows)).toEqual(rank)
    // Worked by hand from network-all-edges' names and counts; ids compare as plain strings, so relationship:10 comes before relationship:3.
    const expected: Record<EdgeSortKey, [string[], string[]]> = {
      first: [refs(5, 3, 4, 6, 7, 8, 10, 9, 11, 12, 13, 14), refs(11, 12, 13, 14, 10, 9, 6, 7, 8, 4, 3, 5)],
      second: [refs(11, 6, 7, 9, 12, 8, 13, 3, 14, 4, 5, 10), refs(10, 5, 14, 4, 3, 13, 12, 8, 7, 9, 6, 11)],
      type: [refs(3, 4, 10, 11, 12, 13, 14, 5, 6, 7, 8, 9), refs(10, 11, 12, 13, 14, 5, 6, 7, 8, 9, 3, 4)],
      evidence: [refs(7, 10, 11, 12, 13, 3, 4, 5, 6, 8, 14, 9), refs(14, 9, 10, 11, 12, 13, 3, 4, 5, 6, 8, 7)],
      assertions: [refs(7, 10, 11, 12, 13, 4, 5, 6, 8, 14, 3, 9), refs(14, 3, 9, 10, 11, 12, 13, 4, 5, 6, 8, 7)],
      publications: [refs(10, 11, 12, 13, 3, 4, 5, 6, 7, 8, 14, 9), refs(14, 9, 10, 11, 12, 13, 3, 4, 5, 6, 7, 8)],
    }
    for (const key of ['first', 'second', 'type', 'evidence', 'assertions', 'publications'] as const) {
      const [ascending, descending] = expected[key]
      const up = sortEdgeRows(rows, { key, direction: 'ascending' })
      const down = sortEdgeRows(rows, { key, direction: 'descending' })
      expect(ids(up), `${key} ascending`).toEqual(ascending)
      expect(ids(down), `${key} descending`).toEqual(descending)
      expect(up).not.toBe(rows)
      expect(down).not.toBe(rows)
    }
    expect(ids(rows)).toEqual(rank)
    const ranked = sortEdgeRows(rows, null)
    expect(ranked).not.toBe(rows)
    expect(ids(ranked)).toEqual(rank)
    expect(ids(sortEdgeRows([...rows].reverse(), null))).toEqual(rank)

    // C-3 `sortSecond`: network-tf1's rank order is 5, 6, 8.
    const tf1 = edgeRows(network('network-tf1'), { showNonSupporting: false, vocabularies: vocabularies() })
    expect(ids(tf1)).toEqual(refs(5, 6, 8))
    expect(ids(sortEdgeRows(tf1, { key: 'second', direction: 'ascending' }))).toEqual(refs(6, 8, 5))
    expect(ids(sortEdgeRows(tf1, { key: 'second', direction: 'descending' }))).toEqual(refs(5, 8, 6))

    // A count that was not requested is null, and null sorts before every number; descending reverses that alone.
    const mixed: EdgeRow[] = [
      ...edgeRows(network('network-p2-tested-absent'), { showNonSupporting: true, vocabularies: vocabularies() }),
      ...tf1,
    ]
    expect(mixed[0].evidenceCount).toBeNull()
    expect(ids(sortEdgeRows(mixed, { key: 'evidence', direction: 'ascending' }))).toEqual(refs(4, 5, 6, 8))
    expect(ids(sortEdgeRows(mixed, { key: 'evidence', direction: 'descending' }))).toEqual(refs(5, 6, 8, 4))
  })

  it("ER-3: nextSort goes none, ascending, descending, none, and a new key starts ascending", () => {
    expect(nextSort(null, 'second')).toEqual({ key: 'second', direction: 'ascending' })
    expect(nextSort({ key: 'second', direction: 'ascending' }, 'second')).toEqual({ key: 'second', direction: 'descending' })
    expect(nextSort({ key: 'second', direction: 'descending' }, 'second')).toBeNull()
    expect(nextSort({ key: 'second', direction: 'descending' }, 'type')).toEqual({ key: 'type', direction: 'ascending' })
    expect(nextSort({ key: 'second', direction: 'ascending' }, 'evidence')).toEqual({ key: 'evidence', direction: 'ascending' })
  })

  it('ER-4: entityRows names every entity in view, in node order, a seed in words', () => {
    const vocab = vocabularies()
    const tf1 = network('network-tf1')
    const rows = entityRows(tf1, { showNonSupporting: false, vocabularies: vocab })
    // A visible relationship names every one of them, so a list of the unlinked ones would be empty.
    expect(ids(rows)).toEqual(['entity:1', 'entity:4', 'entity:7'])
    expect(rows.map((row) => row.name)).toEqual(['SYN-G1 (Gene)', 'SYN-G4 (Gene)', 'SYN-TF1 (Gene), seed'])
    expect(rows[2].node).toBe(nodeIn(tf1, 'entity:7'))

    const inconclusive = entityRows(network('network-p2-inconclusive'), { showNonSupporting: true, vocabularies: vocab })
    expect(inconclusive.map((row) => [row.id, row.name])).toEqual([['entity:12', 'SYN-P2 (Protein (UniProtKB entry)), seed']])
    expect(entityRows(network('network-p2-inconclusive'), { showNonSupporting: false, vocabularies: vocab }).map((row) => row.id))
      .toEqual(['entity:12'])

    const all = network('network-tf1-outcome-all')
    expect(ids(entityRows(all, { showNonSupporting: true, vocabularies: vocab }))).toEqual(['entity:1', 'entity:2', 'entity:4', 'entity:7'])
    expect(ids(entityRows(all, { showNonSupporting: false, vocabularies: vocab }))).toEqual(['entity:1', 'entity:4', 'entity:7'])
    expect(entityRows(null, { showNonSupporting: true, vocabularies: vocab })).toEqual([])
  })

  it('ER-5: every name is unique, twins take their refs as their panels do, and a name is clipped before its suffix', () => {
    const vocab = vocabularies()
    const all = edgeRows(network('network-all-edges'), { showNonSupporting: true, vocabularies: vocab })
    const nameOf = (rows: readonly EdgeRow[], id: string) => rows.find((row) => row.id === id)?.name
    expect(nameOf(all, 'relationship:14')).toBe('Transcriptional regulation: SYN-TF3 (Gene) to SYN-SHARED (Gene)')
    expect(nameOf(all, 'relationship:4'))
      .toBe('Physical interaction between SYN-P2 (Protein (UniProtKB entry)) and SYN-SHARED (Protein (UniProtKB entry))')

    const twins = graphOfNetwork(makeNetworkResponse({ edges: twinEdges }))
    const twinRows = edgeRows(twins, { showNonSupporting: true, vocabularies: vocab })
    expect(twinRows.map((row) => [row.id, row.name])).toEqual([
      ['relationship:931', 'Transcriptional regulation: SYN-N901 (Gene) to SYN-N902 (Gene) (relationship:931)'],
      ['relationship:932', 'Transcriptional regulation: SYN-N901 (Gene) to SYN-N902 (Gene) (relationship:932)'],
    ])
    const kept = keptGraph(twins).graph
    for (const row of twinRows) {
      expect(relationshipHeading(row.id, row.edge, kept.nodes, kept.edges, null, vocab), row.id).toBe(row.name)
    }
    // A twin the toggle hides still names its visible twin apart: the test is over the kept edges.
    const hiddenTwin = makeGraphEdge({
      id: 'relationship:932',
      effect: null,
      effect_counts: { activating: 0, inhibiting: 0, unknown: 0, not_stated: 0 },
      outcome_counts: { supported: 0, tested_absent: 1, inconclusive: 0, not_significant: 0 },
      independent_outcome_counts: { supported: 0, tested_absent: 1, inconclusive: 0, not_significant: 0 },
      assertion_count: 0,
      evidence_count: 0,
      distinct: { publications: 0, releases: 0, datasets: 0 },
      by_basis: {},
    })
    const withHidden = graphOfNetwork(makeNetworkResponse({ edges: [twinEdges[0], hiddenTwin] }))
    expect(edgeRows(withHidden, { showNonSupporting: false, vocabularies: vocab }).map((row) => [row.id, row.name])).toEqual([
      ['relationship:931', 'Transcriptional regulation: SYN-N901 (Gene) to SYN-N902 (Gene) (relationship:931)'],
    ])

    expect(uniqueNames([{ id: 'a', name: 'x' }, { id: 'b', name: 'x' }, { id: 'c', name: 'y' }])).toEqual(['x (a)', 'x (b)', 'y'])
    expect(uniqueNames([])).toEqual([])

    for (const name of recordedNetworks()) {
      for (const showNonSupporting of [true, false]) {
        const edgeNames = edgeRows(network(name), { showNonSupporting, vocabularies: vocab }).map((row) => row.name)
        const entityNames = entityRows(network(name), { showNonSupporting, vocabularies: vocab }).map((row) => row.name)
        expect(new Set(edgeNames).size, `${name} ${showNonSupporting}`).toBe(edgeNames.length)
        expect(new Set(entityNames).size, `${name} ${showNonSupporting}`).toBe(entityNames.length)
      }
    }
    const allEntities = entityRows(network('network-all-edges'), { showNonSupporting: true, vocabularies: vocab })
    expect(allEntities.filter((row) => row.id === 'entity:6' || row.id === 'entity:13').map((row) => row.name))
      .toEqual(['SYN-SHARED (Gene)', 'SYN-SHARED (Protein (UniProtKB entry))'])

    // The seed's words are part of the name uniqueNames compares, so only two seeds of one name take refs.
    const seeds = handGraph([
      gene('entity:951', 'SYN-X'), gene('entity:952', 'SYN-X', true), gene('entity:953', 'SYN-Y', true), gene('entity:954', 'SYN-Y', true),
    ], [])
    expect(entityRows(seeds, { showNonSupporting: false, vocabularies: vocab }).map((row) => row.name)).toEqual([
      'SYN-X (Gene)', 'SYN-X (Gene), seed', 'SYN-Y (Gene), seed (entity:953)', 'SYN-Y (Gene), seed (entity:954)',
    ])

    // Two labels that differ only past the clip are told apart by their refs (M36).
    const clipped = `SYN-${'x'.repeat(996)}… (1005 more characters not shown)`
    const long = handGraph([
      gene('entity:951', `SYN-${'x'.repeat(2000)}A`, true), gene('entity:952', `SYN-${'x'.repeat(2000)}B`, true), gene('entity:953', 'SYN-N953'),
    ], [
      makeGraphEdge({ id: 'relationship:951', source: 'entity:951', target: 'entity:953' }),
      makeGraphEdge({ id: 'relationship:952', source: 'entity:952', target: 'entity:953' }),
    ])
    expect(entityRows(long, { showNonSupporting: false, vocabularies: vocab }).map((row) => row.name)).toEqual([
      `${clipped} (Gene), seed (entity:951)`, `${clipped} (Gene), seed (entity:952)`, 'SYN-N953 (Gene)',
    ])
    expect(edgeRows(long, { showNonSupporting: false, vocabularies: vocab }).map((row) => row.name)).toEqual([
      `Transcriptional regulation: ${clipped} (Gene) to SYN-N953 (Gene) (relationship:951)`,
      `Transcriptional regulation: ${clipped} (Gene) to SYN-N953 (Gene) (relationship:952)`,
    ])
  })

  it('ER-6: the cell words, never "no support" of what was not requested, and each end with its status word', () => {
    const vocab = vocabularies()
    const effects: [GraphEdgeEffect | null, number, string][] = [
      ['activating', 1, 'activates'],
      ['inhibiting', 1, 'inhibits'],
      ['mixed', 2, 'mixed effect'],
      ['unknown', 1, 'effect unknown'],
      ['not_applicable', 1, 'direction only: this type states no effect'],
      // Supporting results pass the filters but were not requested (M3).
      [null, 1, NOT_REQUESTED],
      [null, 0, 'no supporting result in view'],
    ]
    for (const [effect, supported, words] of effects) {
      const edge = makeGraphEdge({ effect, outcome_counts: { supported, tested_absent: 1, inconclusive: 0, not_significant: 0 } })
      expect(effectCell(edge), `${effect} ${supported}`).toBe(words)
    }
    expect(effectCell(unspecifiedEdge)).toBe('not applicable: this type states no effect')
    expect(effectCell(roleOrderedEdge)).toBe('not applicable: this type states no effect')
    expect(effectCell(unknownChannelsEdge)).toBe('syn effect')
    expect(effectCell(makeGraphEdge({ effect: 'constructor' as string as GraphEdgeEffect }))).toBe('constructor')

    const withLigand: GraphVocabularies = { ...vocab, relationship_types: [...vocab.relationship_types, ligandReceptorType] }
    const encodes = recorded<GraphEntityResponse>('entity-tf1').body.reference_relationships[0]
    expect(directionCell(unspecifiedEdge, vocab)).toBe('symmetric: the same claim either way')
    expect(directionCell(makeGraphEdge(), vocab)).toBe('regulator to target')
    expect(directionCell(makeGraphEdge(), null)).toBe('first to second')
    expect(directionCell(roleOrderedEdge, withLigand)).toBe('ligand to receptor, with no claim of cause or effect')
    expect(directionCell(roleOrderedEdge, vocab)).toBe('first to second, with no claim of cause or effect')
    expect(directionCell(encodes, vocab)).toBe('gene to protein entry, with no claim of cause or effect')
    expect(directionCell(unknownChannelsEdge, vocab)).toBe('syn orientation')

    const all = network('network-all-edges')
    const p2 = network('network-p2-tested-absent')
    const physical: [GraphEdge, TypedGraph, string][] = [
      [edgeIn(all, 'relationship:3'), all, 'direct interaction'],
      [edgeIn(all, 'relationship:4'), all, 'physical interaction, level not stated'],
      [edgeIn(p2, 'relationship:4'), p2, 'tested, not detected; tested for: direct interaction'],
      [levelAssignedEdge, all, 'co-complex association, assigned by openPIP from the stated assay'],
      [unspecifiedEdge, all, 'Protein interaction, type not stated'],
    ]
    for (const [edge, graph, words] of physical) {
      expect(levelCell(edge, graph, vocab), edge.id).toBe(words)
      expect(levelCell(edge, graph, vocab), edge.id).toBe(edgeLabel(edge, graph, vocab))
    }
    // The canvas labels these, but they have no level.
    for (const edge of [edgeIn(all, 'relationship:5'), negativeSignEdge, mixedSignEdge, encodes]) {
      expect(edgeLabel(edge, all, vocab), edge.id).not.toBe('')
      expect(levelCell(edge, all, vocab), edge.id).toBe('')
    }

    expect(outcomeCell(edgeIn(all, 'relationship:4'))).toBe('1 supporting; 1 tested, not detected')
    expect(outcomeCell(edgeIn(all, 'relationship:7'))).toBe('1 tested, not detected')
    expect(outcomeCell(makeGraphEdge({ outcome_counts: { supported: 3, tested_absent: 0, inconclusive: 1, not_significant: 2 } })))
      .toBe('3 supporting; 1 inconclusive; 2 not significant at the stated threshold')
    expect(outcomeCell(makeGraphEdge({ outcome_counts: { supported: 0, tested_absent: 0, inconclusive: 0, not_significant: 0 } }))).toBe('')

    expect(countCell(2, ['supported'])).toBe('2')
    expect(countCell(0, ['supported', 'tested_absent'])).toBe('0')
    expect(countCell(2, ['tested_absent'])).toBe(NOT_REQUESTED)
    const [notRequested] = edgeRows(p2, { showNonSupporting: true, vocabularies: vocab })
    expect([notRequested.evidence, notRequested.assertions, notRequested.publications]).toEqual([NOT_REQUESTED, NOT_REQUESTED, NOT_REQUESTED])
    expect([notRequested.evidenceCount, notRequested.assertionCount, notRequested.publicationCount]).toEqual([null, null, null])
    expect(notRequested.effect).toBe('not applicable: this type states no effect')
    const [requested] = edgeRows(network('network-truncated'), { showNonSupporting: false, vocabularies: vocab })
    expect([requested.evidence, requested.assertions, requested.publications]).toEqual(['2', '2', '2'])
    expect([requested.evidenceCount, requested.assertionCount, requested.publicationCount]).toEqual([2, 2, 2])

    const tf1 = nodeIn(network('network-tf1'), 'entity:7')
    expect(tf1.status).toBe('provisional')
    const [nullStatus] = ppiNodes
    expect(nullStatus.status).toBeNull()
    const active: TypedGraphNode = { ...tf1, status: 'active' }
    const merged: TypedGraphNode = { ...tf1, status: 'merged' }
    expect(endCell(tf1, 'entity:7', true, null, vocab)).toBe('SYN-TF1 (Gene), provisional')
    expect(endCell(tf1, 'entity:7', false, null, vocab)).toBe('SYN-TF1 (Gene)')
    expect(endCell(merged, 'entity:7', true, null, vocab)).toBe('SYN-TF1 (Gene), merged')
    expect(endCell(active, 'entity:7', true, null, vocab)).toBe('SYN-TF1 (Gene)')
    expect(endCell(active, 'entity:7', false, null, vocab)).toBe('SYN-TF1 (Gene)')
    expect(endCell(nullStatus, 'ppi-protein:1', true, null, vocab)).toBe('SYN-PPI-1 (Protein (UniProtKB entry))')
    expect(endCell(nullStatus, 'ppi-protein:1', false, null, vocab)).toBe('SYN-PPI-1 (Protein (UniProtKB entry))')
    // Without the vocabularies the type falls back to its slug (M40).
    expect(endCell(nullStatus, 'ppi-protein:1', false)).toBe('SYN-PPI-1 (protein)')
    expect(endCell(undefined, 'ppi-protein:9', true, null, vocab)).toBe('ppi-protein:9')
    expect(endCell(nodeIn(all, 'entity:3'), 'entity:3', false, presentationAllKeys, vocab)).toBe('SYN-G3 (Gene)')
  })
})
