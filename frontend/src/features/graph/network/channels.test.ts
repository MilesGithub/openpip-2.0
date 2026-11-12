import { AxiosError, CanceledError } from 'axios'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { classifyGraphError, GraphBadBodyError, GraphRequestTooLongError } from '../../../api/graph/errors'
import type { GraphErrorInfo } from '../../../api/graph/errors'
import {
  directionOnlyEdge, inconclusiveEdge, levelAssignedEdge, ligandReceptorType, makeGraphEdge, makeGraphNode,
  makeNetworkNode, makeNetworkResponse, mixedReferenceEdge, mixedSignEdge, negativeSignEdge, notSignificantEdge,
  positiveSignEdge, presentationAllKeys, roleOrderedEdge, testedAbsentReferenceEdge, testedAbsentRelease,
  unknownChannelsEdge, unknownLevelEdge, unresolvedReferenceEdge, unspecifiedEdge,
} from '../../../mocks/fixtures/typedGraph/extras'
import { recordedEntries } from '../../../mocks/fixtures/typedGraph/index'
import { recorded } from '../../../test/graphHooks'
import type {
  GraphEdge, GraphEdgeEffect, GraphEntityResponse, GraphFiltersEcho, GraphLevel, GraphNetworkResponse, GraphOrientation,
  GraphOutcome, GraphPresentation, GraphVocabularies,
} from '../types'
import {
  classToken, edgeClasses, edgeLine, edgeMarker, edgeWidthPx, isNonSupport, isPredicted, nodeClasses, nodeShape,
  nonSupportOutcome,
} from './channels'
import type { EdgeLine, EdgeMarker } from './channels'
import { EMPTY_GRAPH, graphOfNetwork, keptGraph, requestedOutcomes } from './graph'
import type { TypedGraph, TypedGraphNode } from './graph'
import { layoutOptions, prefersReducedMotion } from './layout'
import {
  nodeLabel, PRESENTATION_ACCENTS, PRESENTATION_DEFAULT_ACCENT, PRESENTATION_DEFAULT_FILTER_NAMES,
  PRESENTATION_DEFAULT_LAYOUT, PRESENTATION_EVIDENCE_PANELS, PRESENTATION_FILTER_PANELS, PRESENTATION_KEYS,
  PRESENTATION_LAYOUTS, presentationParams, resolveLayout,
} from './presentation'
import type { TypedLayoutName } from './presentation'
import presentationSource from './presentation.ts?raw'
import { defaultShowNonSupporting, visibleGraph } from './visibility'
import {
  EFFECT_WORDS, edgeDescription, edgeLabel, entityTypeLabel, LEVEL_ASSIGNED_WORDS, LEVEL_WORDS, levelWords,
  networkErrorText, networkStatusText, OUTCOME_LINE_WORDS, plural, roleLabels, SIGN_WORDS, typeLabel,
} from './words'

// Cytoscape and React report a bad value through the console instead of throwing.
const REPORTED = /cytoscape|is invalid|not a valid|act\(/i

beforeEach(() => {
  vi.spyOn(console, 'warn')
  vi.spyOn(console, 'error')
})

afterEach(() => {
  const calls = [...vi.mocked(console.warn).mock.calls, ...vi.mocked(console.error).mock.calls]
  vi.restoreAllMocks()
  expect(calls.map((args) => args.map(String).join(' ')).filter((text) => REPORTED.test(text))).toEqual([])
})

const ALL_OUTCOMES: readonly GraphOutcome[] = ['supported', 'tested_absent', 'inconclusive', 'not_significant']
const NO_OUTCOMES = { supported: 0, tested_absent: 0, inconclusive: 0, not_significant: 0 }

function network(name: string): TypedGraph {
  return graphOfNetwork(recorded<GraphNetworkResponse>(name).body)
}

function edgeIn(graph: TypedGraph, id: string): GraphEdge {
  const edge = graph.edges.find((candidate) => candidate.id === id)
  if (edge === undefined) throw new Error(`no ${id} in the graph`)
  return edge
}

function nodeIn(graph: TypedGraph, id: string): TypedGraphNode {
  const node = graph.nodes.find((candidate) => candidate.id === id)
  if (node === undefined) throw new Error(`no ${id} in the graph`)
  return node
}

/** An entity page's reference links name its nodes and releases, which their words read. */
function entityGraph(name: string): TypedGraph {
  const body = recorded<GraphEntityResponse>(name).body
  return { ...EMPTY_GRAPH, nodes: body.nodes, edges: body.reference_relationships, releases: body.releases }
}

function encodes(): GraphEdge {
  return recorded<GraphEntityResponse>('entity-tf1').body.reference_relationships[0]
}

function vocabularies(): GraphVocabularies {
  return recorded<GraphVocabularies>('vocabularies').body
}

function withLigand(): GraphVocabularies {
  const vocab = vocabularies()
  return { ...vocab, relationship_types: [...vocab.relationship_types, ligandReceptorType] }
}

/** One SYN- node per endpoint, the first a seed, answering a request for `outcome`. */
function handGraph(edges: GraphEdge[], outcome: GraphFiltersEcho['outcome'] = null): TypedGraph {
  const ids = Array.from(new Set(edges.flatMap((edge) => [edge.source, edge.target])))
  const nodes = ids.map((id, index) => makeNetworkNode({
    id,
    label: `SYN-N${index}`,
    primary_identifier: { namespace: 'synthetic_gene', value: `SYN-N${index}` },
    is_seed: index === 0,
  }))
  const response = makeNetworkResponse()
  return graphOfNetwork({ ...response, query: { ...response.query, outcome }, nodes, edges })
}

/** handGraph answering a request for every outcome, holding both releases mixedReferenceEdge lists. */
function everyOutcomeGraph(edges: GraphEdge[]): TypedGraph {
  const graph = handGraph(edges, ['all'])
  return { ...graph, releases: [...graph.releases, testedAbsentRelease] }
}

/** Listed without support, as a request for other outcomes lists an edge. */
function withoutSupport(id: string, counts: Partial<Record<GraphOutcome, number>>, extra: Partial<GraphEdge> = {}): GraphEdge {
  const outcomes = { ...NO_OUTCOMES, ...counts }
  return makeGraphEdge({
    id,
    effect: null,
    effect_counts: { activating: 0, inhibiting: 0, unknown: 0, not_stated: 0 },
    outcome_counts: outcomes,
    independent_outcome_counts: { ...outcomes },
    assertion_count: 0,
    evidence_count: 0,
    distinct: { publications: 0, releases: 0, datasets: 0 },
    by_basis: {},
    ...extra,
  })
}

const noCountsEdge = withoutSupport('relationship:933', {})

describe('the marker table', () => {
  it('draws the chevron on a reference link, which is role-ordered, never the circle', () => {
    const edge = encodes()
    expect([edge.family, edge.orientation]).toEqual(['reference', 'role_ordered'])
    expect(edgeMarker(edge)).toBe('chevron-reference')
  })

  it.each<[string, GraphEdge, EdgeMarker]>([
    ['no support wins over the reference family', { ...encodes(), assertion_count: 0 }, 'none'],
    ['a reference pair listed only for a tested-absent result', testedAbsentReferenceEdge, 'none'],
    ['relationship:7, listed without support', edgeIn(network('network-all-edges'), 'relationship:7'), 'none'],
    ['no support wins over an effect', makeGraphEdge({ assertion_count: 0 }), 'none'],
    ['no support wins over role order', { ...roleOrderedEdge, assertion_count: 0 }, 'none'],
    ['relationship:3, symmetric', edgeIn(network('network-all-edges'), 'relationship:3'), 'none'],
    ['symmetric wins over an effect', makeGraphEdge({ orientation: 'symmetric' }), 'none'],
    ['a role-ordered type', roleOrderedEdge, 'circle'],
    ['directed and activating', makeGraphEdge({ effect: 'activating' }), 'triangle'],
    ['directed and inhibiting', makeGraphEdge({ effect: 'inhibiting' }), 'tee'],
    ['directed and mixed', makeGraphEdge({ effect: 'mixed' }), 'diamond'],
    ['directed and unknown', makeGraphEdge({ effect: 'unknown' }), 'vee-hollow'],
    ['directed without effect polarity', directionOnlyEdge, 'square-direction'],
    ['an unknown orientation', makeGraphEdge({ orientation: 'syn_orientation' as string as GraphOrientation }), 'none'],
    ['an unknown effect', makeGraphEdge({ effect: 'syn_effect' as string as GraphEdgeEffect }), 'none'],
    ['an unknown family, orientation and effect', unknownChannelsEdge, 'none'],
    ['a directed edge whose effect is null', makeGraphEdge({ effect: null }), 'none'],
    ['a negative genetic sign never draws a tee', makeGraphEdge({ genetic_sign: 'negative' }), 'triangle'],
    ['a sign on a symmetric type', negativeSignEdge, 'none'],
  ])('%s', (_name, edge, marker) => {
    expect(edgeMarker(edge)).toBe(marker)
  })

  it('on network-all-edges', () => {
    const graph = network('network-all-edges')
    expect(Object.fromEntries(graph.edges.map((edge) => [edge.id, edgeMarker(edge)]))).toEqual({
      'relationship:10': 'triangle',
      'relationship:11': 'triangle',
      'relationship:14': 'triangle',
      'relationship:5': 'tee',
      'relationship:6': 'tee',
      'relationship:9': 'diamond',
      'relationship:8': 'vee-hollow',
      'relationship:12': 'vee-hollow',
      'relationship:13': 'vee-hollow',
      'relationship:3': 'none',
      'relationship:4': 'none',
      'relationship:7': 'none',
    })
  })

  it.each([
    ['network-tf1-v1', 'triangle'],
    ['network-tf1', 'tee'],
    ['network-tf1-both-releases', 'diamond'],
  ])('relationship:6 in %s', (name, marker) => {
    expect(edgeMarker(edgeIn(network(name), 'relationship:6'))).toBe(marker)
  })
})

describe('the line table', () => {
  it('relationship:4 is solid with its supporting result, and dashed when only tested-absent results were asked for', () => {
    const all = network('network-all-edges')
    const supported = edgeIn(all, 'relationship:4')
    expect([supported.assertion_count, supported.outcome_counts.tested_absent]).toEqual([1, 1])
    expect(isNonSupport(supported)).toBe(false)
    expect(edgeLine(supported, all.requestedOutcomes)).toBe('supported')
    const p2 = network('network-p2-tested-absent')
    const absent = edgeIn(p2, 'relationship:4')
    expect(isNonSupport(absent)).toBe(true)
    expect(edgeLine(absent, p2.requestedOutcomes)).toBe('tested-absent')
  })

  it.each<[string, GraphEdge, readonly GraphOutcome[], EdgeLine]>([
    [
      'relationship:7 under outcome=all',
      edgeIn(network('network-tf1-outcome-all'), 'relationship:7'),
      network('network-tf1-outcome-all').requestedOutcomes,
      'tested-absent',
    ],
    ['relationship:13, predicted only', edgeIn(network('network-all-edges'), 'relationship:13'), ALL_OUTCOMES, 'predicted'],
    ['relationship:14, experimental', edgeIn(network('network-all-edges'), 'relationship:14'), ALL_OUTCOMES, 'supported'],
    ['the not-significant edge', notSignificantEdge, ALL_OUTCOMES, 'not-significant'],
    ['the inconclusive edge', inconclusiveEdge, ALL_OUTCOMES, 'inconclusive'],
    ['the not-significant edge, its outcome not asked for', notSignificantEdge, ['supported'], 'not-significant'],
    ['no support and no outcome counted', noCountsEdge, ALL_OUTCOMES, 'non-support'],
    ['a reference link', encodes(), ['supported'], 'reference'],
    ['a reference pair listed only for a tested-absent result', testedAbsentReferenceEdge, ['tested_absent'], 'tested-absent'],
    ['a reference link stated by prediction', { ...encodes(), by_basis: { computational_prediction: 1 } }, ['supported'], 'reference'],
    [
      'no support wins over prediction',
      withoutSupport('relationship:934', { tested_absent: 1 }, { by_basis: { computational_prediction: 1 } }),
      ['tested_absent'],
      'tested-absent',
    ],
  ])('%s', (_name, edge, requested, line) => {
    expect(edgeLine(edge, requested)).toBe(line)
  })

  it.each<[string, GraphEdge['by_basis'], boolean]>([
    ['prediction alone', { computational_prediction: 1 }, true],
    ['prediction and an experiment', { computational_prediction: 2, experimental: 1 }, false],
    ['inference alone', { computational_inference: 1 }, false],
    ['no basis', {}, false],
  ])('isPredicted: %s', (_name, byBasis, predicted) => {
    expect(isPredicted(makeGraphEdge({ by_basis: byBasis }))).toBe(predicted)
  })

  it('nonSupportOutcome takes tested-absent, then not significant, then inconclusive, then falls back', () => {
    const three = withoutSupport('relationship:935', { tested_absent: 1, inconclusive: 1, not_significant: 1 })
    expect(nonSupportOutcome(three, ALL_OUTCOMES)).toBe('tested_absent')
    expect(nonSupportOutcome(three, ['inconclusive', 'not_significant'])).toBe('not_significant')
    expect(nonSupportOutcome(three, ['inconclusive'])).toBe('inconclusive')
    const later = withoutSupport('relationship:936', { inconclusive: 1 })
    expect(nonSupportOutcome(later, ['tested_absent', 'inconclusive'])).toBe('inconclusive')
    const unasked = withoutSupport('relationship:937', { inconclusive: 1, not_significant: 1 })
    expect(nonSupportOutcome(unasked, ['supported'])).toBe('not_significant')
    expect(nonSupportOutcome(unasked, ['tested_absent'])).toBe('not_significant')
    expect(nonSupportOutcome(noCountsEdge, ALL_OUTCOMES)).toBeNull()
  })
})

describe('the graph of a response', () => {
  it.each<[string, GraphFiltersEcho['outcome'], GraphOutcome[]]>([
    ['no outcome filter', null, ['supported']],
    ['all', ['all'], ['supported', 'tested_absent', 'inconclusive', 'not_significant']],
    ['a list holding all', ['tested_absent', 'all'], ['supported', 'tested_absent', 'inconclusive', 'not_significant']],
    ['a repeated outcome', ['tested_absent', 'tested_absent'], ['tested_absent']],
    ['a list out of order', ['not_significant', 'supported', 'inconclusive'], ['supported', 'inconclusive', 'not_significant']],
  ])('requestedOutcomes: %s', (_name, outcome, expected) => {
    expect(requestedOutcomes(outcome)).toEqual(expected)
  })

  it("graphOfNetwork keeps the response's own arrays and its request", () => {
    const body = recorded<GraphNetworkResponse>('network-tf1-outcome-all').body
    const graph = graphOfNetwork(body)
    expect(graph.nodes).toBe(body.nodes)
    expect(graph.edges).toBe(body.edges)
    expect(graph.releases).toBe(body.releases)
    expect(graph.query).toBe(body.query)
    expect(graph.page).toBe(body.page)
    expect(graph.warnings).toBe(body.warnings)
    expect(graph.requestedOutcomes).toEqual(['supported', 'tested_absent', 'inconclusive', 'not_significant'])
  })

  it('keptGraph returns the graph itself when nothing is dropped', () => {
    for (const name of ['network-tf1', 'network-all-edges', 'network-p2-inconclusive']) {
      const graph = network(name)
      const kept = keptGraph(graph)
      expect(kept.graph).toBe(graph)
      expect(kept.dropped).toEqual([])
    }
  })

  it('keptGraph drops repeated and reserved ids and edges to an entity it does not keep, the first of a repeat winning', () => {
    const graph = network('network-tf1')
    const first = nodeIn(graph, 'entity:1')
    const edge = edgeIn(graph, 'relationship:5')
    const odd: TypedGraph = {
      ...graph,
      nodes: [...graph.nodes, { ...first, label: 'SYN-REPEAT' }, makeNetworkNode({ id: 'constructor', label: 'SYN-RESERVED' })],
      edges: [
        ...graph.edges,
        { ...edge, target: 'entity:4' },
        { ...edge, id: 'entity:7' },
        { ...edge, id: 'toString' },
        { ...edge, id: 'relationship:999', target: 'entity:999' },
        { ...edge, id: 'relationship:998', source: 'constructor' },
      ],
    }
    const kept = keptGraph(odd)
    expect(kept.dropped).toHaveLength(7)
    expect(kept.dropped).toEqual(expect.arrayContaining([
      { id: 'entity:1', kind: 'node', reason: 'duplicate-id' },
      { id: 'constructor', kind: 'node', reason: 'reserved-id' },
      { id: 'relationship:5', kind: 'edge', reason: 'duplicate-id' },
      { id: 'entity:7', kind: 'edge', reason: 'duplicate-id' },
      { id: 'toString', kind: 'edge', reason: 'reserved-id' },
      { id: 'relationship:999', kind: 'edge', reason: 'missing-endpoint' },
      { id: 'relationship:998', kind: 'edge', reason: 'missing-endpoint' },
    ]))
    expect(kept.graph.nodes.map((node) => node.id)).toEqual(['entity:1', 'entity:4', 'entity:7'])
    expect(kept.graph.nodes[0]).toBe(first)
    expect(kept.graph.edges.map((element) => element.id)).toEqual(['relationship:5', 'relationship:6', 'relationship:8'])
    expect(kept.graph.edges[0]).toBe(edge)
    expect([kept.graph.releases, kept.graph.query, kept.graph.page]).toEqual([graph.releases, graph.query, graph.page])
  })
})

describe('width', () => {
  it.each([
    [0, 1.5],
    [1, 1.5],
    [2, 2.5],
    [3, 3.5],
    [5, 5.5],
    [9, 5.5],
  ])('a supported edge with %i publications is %f px', (publications, width) => {
    expect(edgeWidthPx(makeGraphEdge({ distinct: { publications, releases: 1, datasets: 1 } }), ['supported'])).toBe(width)
  })

  it.each<[string, GraphEdge, readonly GraphOutcome[], number]>([
    [
      'a predicted edge with 3 publications',
      makeGraphEdge({ by_basis: { computational_prediction: 1 }, distinct: { publications: 3, releases: 1, datasets: 1 } }),
      ['supported'],
      3.5,
    ],
    [
      'a tested-absent edge, whatever its publications',
      withoutSupport('relationship:938', { tested_absent: 1 }, { distinct: { publications: 9, releases: 1, datasets: 1 } }),
      ['tested_absent'],
      1.5,
    ],
    ['the not-significant edge', notSignificantEdge, ALL_OUTCOMES, 1],
    ['the inconclusive edge', inconclusiveEdge, ALL_OUTCOMES, 1],
    ['no support and no outcome counted', noCountsEdge, ALL_OUTCOMES, 1],
    ['a reference link with 3 publications', { ...encodes(), distinct: { publications: 3, releases: 1, datasets: 1 } }, ['supported'], 1],
  ])('%s', (_name, edge, requested, width) => {
    expect(edgeWidthPx(edge, requested)).toBe(width)
  })
})

describe('classes', () => {
  it.each<[string, GraphEdge, readonly GraphOutcome[], string[]]>([
    [
      'relationship:7 under outcome=all',
      edgeIn(network('network-tf1-outcome-all'), 'relationship:7'),
      ALL_OUTCOMES,
      ['rt-transcriptional_regulation', 'fam-regulation', 'directed', 'out-tested-absent', 'synthetic', 'mk-none', 'ln-tested-absent'],
    ],
    [
      'relationship:13, predicted',
      edgeIn(network('network-all-edges'), 'relationship:13'),
      ALL_OUTCOMES,
      [
        'rt-transcriptional_regulation', 'fam-regulation', 'directed', 'eff-unknown', 'basis-predicted', 'synthetic',
        'mk-vee-hollow', 'ln-predicted',
      ],
    ],
    [
      'relationship:9, mixed',
      edgeIn(network('network-all-edges'), 'relationship:9'),
      ALL_OUTCOMES,
      ['rt-transcriptional_regulation', 'fam-regulation', 'directed', 'eff-mixed', 'synthetic', 'mk-diamond', 'ln-supported'],
    ],
    [
      'relationship:3, symmetric',
      edgeIn(network('network-all-edges'), 'relationship:3'),
      ALL_OUTCOMES,
      ['rt-physical_interaction', 'fam-physical', 'symmetric', 'synthetic', 'mk-none', 'ln-supported'],
    ],
    [
      'a reference link',
      encodes(),
      ['supported'],
      ['rt-encodes', 'fam-reference', 'role-ordered', 'synthetic', 'mk-chevron-reference', 'ln-reference'],
    ],
    [
      'a reference pair listed only for a tested-absent result',
      testedAbsentReferenceEdge,
      ['tested_absent'],
      ['rt-encodes', 'fam-reference', 'role-ordered', 'out-tested-absent', 'synthetic', 'mk-none', 'ln-tested-absent'],
    ],
    [
      'a negative sign',
      negativeSignEdge,
      ['supported'],
      ['rt-syn_genetic', 'fam-genetic', 'symmetric', 'gsign-negative', 'synthetic', 'mk-none', 'ln-supported'],
    ],
    [
      'a positive sign',
      positiveSignEdge,
      ['supported'],
      ['rt-syn_genetic', 'fam-genetic', 'symmetric', 'gsign-positive', 'synthetic', 'mk-none', 'ln-supported'],
    ],
    [
      'a mixed sign',
      mixedSignEdge,
      ['supported'],
      ['rt-syn_genetic', 'fam-genetic', 'symmetric', 'gsign-mixed', 'synthetic', 'mk-none', 'ln-supported'],
    ],
    [
      'the not-significant edge',
      notSignificantEdge,
      ALL_OUTCOMES,
      ['rt-transcriptional_regulation', 'fam-regulation', 'directed', 'out-not-significant', 'synthetic', 'mk-none', 'ln-not-significant'],
    ],
    [
      'the inconclusive edge',
      inconclusiveEdge,
      ALL_OUTCOMES,
      ['rt-transcriptional_regulation', 'fam-regulation', 'directed', 'out-inconclusive', 'synthetic', 'mk-none', 'ln-inconclusive'],
    ],
    [
      'no support and no outcome counted',
      noCountsEdge,
      ALL_OUTCOMES,
      ['rt-transcriptional_regulation', 'fam-regulation', 'directed', 'synthetic', 'mk-none', 'ln-non-support'],
    ],
    [
      'the role-ordered edge',
      roleOrderedEdge,
      ['supported'],
      ['rt-syn_ligand_receptor', 'fam-ligand_receptor', 'role-ordered', 'synthetic', 'mk-circle', 'ln-supported'],
    ],
    [
      'unknown channels',
      unknownChannelsEdge,
      ['supported'],
      ['rt-syn_unknown_type', 'fam-syn_family', 'synthetic', 'mk-none', 'ln-supported'],
    ],
    [
      'a slug that is no class name, on data that is not synthetic',
      makeGraphEdge({ type: 'syn:odd type', is_synthetic: false }),
      ['supported'],
      ['rt-syn_odd_type', 'fam-regulation', 'directed', 'eff-activating', 'mk-triangle', 'ln-supported'],
    ],
  ])('edgeClasses: %s', (_name, edge, requested, classes) => {
    expect(edgeClasses(edge, requested)).toEqual(classes)
  })

  it('gives each recorded edge each class once, with one marker class and one line class', () => {
    const graph = network('network-all-edges')
    expect(graph.edges).toHaveLength(12)
    for (const edge of graph.edges) {
      const classes = edgeClasses(edge, graph.requestedOutcomes)
      expect(new Set(classes).size, edge.id).toBe(classes.length)
      expect(classes.filter((name) => name.startsWith('mk-')), edge.id).toHaveLength(1)
      expect(classes.filter((name) => name.startsWith('ln-')), edge.id).toHaveLength(1)
    }
  })

  it.each<[string, TypedGraphNode, string[]]>([
    ['a seed gene', nodeIn(network('network-tf1'), 'entity:7'), ['et-gene', 'sh-gene', 'seed']],
    ['a gene', nodeIn(network('network-tf1'), 'entity:1'), ['et-gene', 'sh-gene']],
    ['a seed protein', nodeIn(network('network-p1-reference'), 'entity:10'), ['et-protein', 'sh-protein', 'seed']],
    ['a protein', nodeIn(network('network-p1-reference'), 'entity:12'), ['et-protein', 'sh-protein']],
    ['an isoform', nodeIn(network('network-p1-reference'), 'entity:11'), ['et-protein_isoform', 'sh-isoform']],
    ['another type, as a class name', makeNetworkNode({ type: 'syn:chemical' }), ['et-syn_chemical', 'sh-other']],
    ['a node with no seed key', makeGraphNode(), ['et-gene', 'sh-gene']],
  ])('nodeClasses: %s', (_name, node, classes) => {
    expect(nodeClasses(node)).toEqual(classes)
  })

  it.each([
    ['gene', 'gene'],
    ['protein', 'protein'],
    ['protein_isoform', 'isoform'],
    ['chemical', 'other'],
    ['protein_complex', 'other'],
  ])('nodeShape: %s', (type, shape) => {
    expect(nodeShape(makeGraphNode({ type }))).toBe(shape)
  })

  it('classToken', () => {
    expect(classToken('a:b c.d/é')).toBe('a_b_c_d__')
    expect(classToken('Syn-Type_9')).toBe('Syn-Type_9')
  })
})

describe('the words of each drawn channel', () => {
  it("D20's level strings, and the words of a level openPIP assigned", () => {
    expect(LEVEL_WORDS).toEqual({
      association: 'association (co-purification)',
      physical_association: 'physical association',
      direct_interaction: 'direct interaction',
      not_stated: 'physical interaction, level not stated',
    })
    for (const vocab of [null, vocabularies()]) {
      expect(levelWords('association', vocab)).toBe('association (co-purification)')
      expect(levelWords('physical_association', vocab)).toBe('physical association')
      expect(levelWords('direct_interaction', vocab)).toBe('direct interaction')
      expect(levelWords('not_stated', vocab)).toBe('physical interaction, level not stated')
    }
    expect(LEVEL_ASSIGNED_WORDS).toBe('co-complex association, assigned by openPIP from the stated assay')
  })

  it('the outcome, sign and effect words', () => {
    expect(OUTCOME_LINE_WORDS).toEqual({
      tested_absent: 'tested, not detected',
      not_significant: 'not significant at the stated threshold',
      inconclusive: 'inconclusive',
    })
    expect(SIGN_WORDS).toEqual({ negative: 'negative (aggravating)', positive: 'positive (alleviating)', mixed: 'mixed sign' })
    expect(EFFECT_WORDS).toEqual({
      activating: 'activates',
      inhibiting: 'inhibits',
      mixed: 'mixed effect',
      unknown: 'effect unknown',
      not_applicable: 'direction only: this type states no effect',
    })
  })

  it('a value the words do not know comes from the vocabularies, then from its slug, never from a prototype', () => {
    const vocab = vocabularies()
    const withLevel: GraphVocabularies = {
      ...vocab,
      interaction_levels: [...vocab.interaction_levels, { value: 'syn_level' as string as GraphLevel, label: 'synthetic level', strength: 9 }],
    }
    expect(levelWords('syn_level', withLevel)).toBe('synthetic level')
    expect(levelWords('syn_level', vocab)).toBe('syn level')
    expect(levelWords('constructor')).toBe('constructor')
    expect(typeLabel('transcriptional_regulation', vocab)).toBe('Transcriptional regulation')
    expect(typeLabel('syn_unknown_type', vocab)).toBe('syn unknown type')
    expect(typeLabel('constructor')).toBe('constructor')
    expect(roleLabels('encodes', vocab)).toEqual({ subject: 'gene', object: 'protein entry' })
    expect(roleLabels('encodes')).toBeNull()
    expect(entityTypeLabel('protein', vocab)).toBe('Protein (UniProtKB entry)')
    expect(entityTypeLabel('syn_entity_type', vocab)).toBe('syn entity type')
  })
})

describe('edgeLabel', () => {
  const tf1Entity = entityGraph('entity-tf1')
  const superseded: TypedGraph = { ...tf1Entity, releases: tf1Entity.releases.map((release) => ({ ...release, superseded: true })) }
  const twoLevels = withoutSupport('relationship:939', { tested_absent: 1 }, {
    tested_levels: { tested_absent: ['direct_interaction', 'association'] },
  })
  // No outcome counted at all, which only hand-built data has; its release resolves in handGraph.
  const uncountedReference = withoutSupport('relationship:940', {}, {
    type: 'encodes', family: 'reference', orientation: 'role_ordered', effect: 'not_applicable',
  })

  it.each<[string, GraphEdge, TypedGraph, GraphVocabularies | null, string]>([
    ['a reference link names its type and release', encodes(), tf1Entity, vocabularies(), 'Encodes · Synthetic regulation example v2'],
    [
      'a superseded release says so',
      encodes(),
      superseded,
      vocabularies(),
      'Encodes · Synthetic regulation example v2 (superseded)',
    ],
    ['no " · " when no release resolves', unresolvedReferenceEdge, tf1Entity, vocabularies(), 'Encodes'],
    [
      'a reference pair listed only for a tested-absent result names its outcome, not its release',
      testedAbsentReferenceEdge,
      handGraph([testedAbsentReferenceEdge], ['tested_absent']),
      vocabularies(),
      'tested, not detected',
    ],
    [
      'a reference pair without covered support and with no outcome counted names no release',
      uncountedReference,
      handGraph([uncountedReference]),
      vocabularies(),
      '',
    ],
    [
      "releases in the edge's order, each of which states it",
      // Two versions of one dataset each state it: two supporting results, one independent, as the
      // API counts relationship:5 here. The reversed order, which the API never lists, tells the
      // edge's order from the response's.
      {
        ...encodes(),
        releases: ['release:2', 'release:1'],
        assertion_count: 2,
        outcome_counts: { ...encodes().outcome_counts, supported: 2 },
        effect_counts: { ...encodes().effect_counts, not_stated: 2 },
        sign_counts: { ...encodes().sign_counts, not_stated: 2 },
        by_basis: { unknown: 2 },
        distinct: { ...encodes().distinct, releases: 2 },
      },
      network('network-tf1-both-releases'),
      vocabularies(),
      'Encodes · Synthetic regulation example v2, Synthetic regulation example v1 (superseded)',
    ],
    [
      'a reference link listed with a release that reports it only as tested and not detected names no release',
      mixedReferenceEdge,
      everyOutcomeGraph([mixedReferenceEdge]),
      vocabularies(),
      'Encodes',
    ],
    [
      'relationship:4 when only tested-absent results were asked for',
      edgeIn(network('network-p2-tested-absent'), 'relationship:4'),
      network('network-p2-tested-absent'),
      null,
      'tested, not detected; tested for: direct interaction',
    ],
    [
      'relationship:7 under outcome=all',
      edgeIn(network('network-tf1-outcome-all'), 'relationship:7'),
      network('network-tf1-outcome-all'),
      null,
      'tested, not detected',
    ],
    [
      'two levels tested for',
      twoLevels,
      handGraph([twoLevels], ['tested_absent']),
      null,
      'tested, not detected; tested for: direct interaction, association (co-purification)',
    ],
    ['not significant', notSignificantEdge, handGraph([notSignificantEdge], ['all']), null, 'not significant at the stated threshold'],
    ['inconclusive', inconclusiveEdge, handGraph([inconclusiveEdge], ['all']), null, 'inconclusive'],
    [
      'protein_interaction_unspecified, by its type label',
      unspecifiedEdge,
      handGraph([unspecifiedEdge]),
      vocabularies(),
      'Protein interaction, type not stated',
    ],
    [
      'protein_interaction_unspecified without vocabularies',
      unspecifiedEdge,
      handGraph([unspecifiedEdge]),
      null,
      'protein interaction unspecified',
    ],
    ['relationship:3, a stated level', edgeIn(network('network-all-edges'), 'relationship:3'), network('network-all-edges'), null, 'direct interaction'],
    [
      'relationship:4, its level not stated',
      edgeIn(network('network-all-edges'), 'relationship:4'),
      network('network-all-edges'),
      null,
      'physical interaction, level not stated',
    ],
    [
      'a level openPIP assigned',
      levelAssignedEdge,
      handGraph([levelAssignedEdge]),
      vocabularies(),
      'co-complex association, assigned by openPIP from the stated assay',
    ],
    ['relationship:5 inhibits', edgeIn(network('network-all-edges'), 'relationship:5'), network('network-all-edges'), null, 'inhibits'],
    ['a negative sign', negativeSignEdge, handGraph([negativeSignEdge]), null, 'negative (aggravating)'],
    ['a positive sign', positiveSignEdge, handGraph([positiveSignEdge]), null, 'positive (alleviating)'],
    ['a mixed sign', mixedSignEdge, handGraph([mixedSignEdge]), null, 'mixed sign'],
    ['an activating edge has none', edgeIn(network('network-all-edges'), 'relationship:14'), network('network-all-edges'), null, ''],
    ['an unknown effect has none', edgeIn(network('network-all-edges'), 'relationship:8'), network('network-all-edges'), null, ''],
  ])('%s', (_name, edge, graph, vocab, label) => {
    expect(edgeLabel(edge, graph, vocab)).toBe(label)
  })
})

describe('edgeDescription', () => {
  it('relationship:3 says nothing of direction or effect', () => {
    const all = network('network-all-edges')
    for (const vocab of [null, vocabularies()]) {
      const text = edgeDescription(edgeIn(all, 'relationship:3'), all, null, vocab)
      expect(text).not.toContain('direction')
      expect(text).not.toContain('effect')
    }
    expect(edgeDescription(edgeIn(all, 'relationship:3'), all, null, vocabularies())).toBe(
      'Physical interaction between SYN-P1 and SYN-P2; direct interaction; 0 distinct publications; synthetic demonstration data.',
    )
  })

  it('relationship:4, listed for its tested-absent result, has no supporting evidence in view and one supporting result', () => {
    const p2 = network('network-p2-tested-absent')
    const text = edgeDescription(edgeIn(p2, 'relationship:4'), p2, null, vocabularies())
    expect(text).toContain('no supporting evidence in view')
    expect(text).toContain('also 1 supporting result')
    expect(text).toBe(
      'Physical interaction between SYN-P2 and SYN-SHARED; no supporting evidence in view; tested, not detected; '
      + 'tested for: direct interaction; also 1 supporting result, not among the requested outcomes; '
      + '0 distinct publications; synthetic demonstration data.',
    )
  })

  it('relationship:1, a reference link, says no "direction only" and names its entities', () => {
    const tf1 = entityGraph('entity-tf1')
    const edge = edgeIn(tf1, 'relationship:1')
    for (const vocab of [null, vocabularies()]) {
      const text = edgeDescription(edge, tf1, null, vocab)
      expect(text).not.toContain('direction only')
      expect(text).toContain('SYN-TF1')
      expect(text).toContain('SYN-P1')
    }
    expect(edgeDescription(edge, tf1, null, vocabularies())).toBe(
      'Encodes: SYN-TF1 (gene) to SYN-P1 (protein entry); stated by Synthetic regulation example v2; '
      + '0 distinct publications; synthetic demonstration data.',
    )
  })

  it('names a type once: an unresolved reference link and the unspecified type add no label', () => {
    expect(edgeDescription(unresolvedReferenceEdge, handGraph([unresolvedReferenceEdge]), null, vocabularies())).toBe(
      'Encodes: SYN-N0 (gene) to SYN-N1 (protein entry); 0 distinct publications; synthetic demonstration data.',
    )
    expect(edgeDescription(unspecifiedEdge, handGraph([unspecifiedEdge]), null, vocabularies())).toBe(
      'Protein interaction, type not stated between SYN-N0 and SYN-N1; 1 distinct publication; synthetic demonstration data.',
    )
  })

  it('words a reference pair listed only for a tested-absent result as that result, naming no release as stating it', () => {
    const graph = handGraph([testedAbsentReferenceEdge], ['tested_absent'])
    expect(edgeDescription(testedAbsentReferenceEdge, graph, null, vocabularies())).toBe(
      'Encodes: SYN-N0 (gene) to SYN-N1 (protein entry); no supporting evidence in view; tested, not detected; '
      + '0 distinct publications; synthetic demonstration data.',
    )
  })

  it('names no release as stating a reference link listed with a release whose only result in view does not support it', () => {
    const text = edgeDescription(mixedReferenceEdge, everyOutcomeGraph([mixedReferenceEdge]), null, vocabularies())
    expect(text).not.toContain('stated by')
    expect(text).toContain('also 1 result tested and not detected')
    expect(text).toBe(
      'Encodes: SYN-N0 (gene) to SYN-N1 (protein entry); also 1 result tested and not detected; '
      + '0 distinct publications; synthetic demonstration data.',
    )
  })

  it('says that a predicted-only edge is a prediction, as its dotted line does, and no other edge', () => {
    const all = network('network-all-edges')
    expect(edgeDescription(edgeIn(all, 'relationship:13'), all, null, vocabularies())).toBe(
      'Transcriptional regulation: SYN-TF3 (regulator) to SYN-G5 (target); effect unknown; '
      + 'supporting results in view: 1 does not state an effect; every supporting result in view is a computational prediction; '
      + '0 distinct publications; synthetic demonstration data.',
    )
    const predictedAbsent = withoutSupport('relationship:934', { tested_absent: 1 }, { by_basis: { computational_prediction: 1 } })
    const predictedLink = { ...encodes(), by_basis: { computational_prediction: 1 } }
    for (const [edge, graph] of [
      [edgeIn(all, 'relationship:12'), all],
      [predictedAbsent, handGraph([predictedAbsent], ['tested_absent'])],
      [predictedLink, entityGraph('entity-tf1')],
    ] as const) {
      expect(edgeDescription(edge, graph, null, vocabularies()), edge.id).not.toContain('prediction')
    }
    const networks = recordedEntries
      .filter((entry) => entry.path === '/api/graph/v1/network' && entry.status === 200)
      .map((entry) => entry.name)
    expect(networks).toContain('network-all-edges')
    const saying = networks.flatMap((name) => {
      const graph = network(name)
      return graph.edges
        .filter((edge) => edgeDescription(edge, graph, null, vocabularies()).includes('prediction'))
        .map((edge) => `${name} ${edge.id}`)
    })
    expect(saying).toEqual(['network-all-edges relationship:13'])
  })

  it.each([
    [
      'relationship:9',
      '1 states activation, 1 states inhibition',
      'Transcriptional regulation: SYN-TF2 (regulator) to SYN-G2 (target); mixed effect; '
      + 'supporting results in view: 1 states activation, 1 states inhibition; 2 distinct publications; synthetic demonstration data.',
    ],
    [
      'relationship:8',
      'an unknown effect',
      'Transcriptional regulation: SYN-TF1 (regulator) to SYN-G4 (target); effect unknown; '
      + 'supporting results in view: 1 states an unknown effect; 0 distinct publications; synthetic demonstration data.',
    ],
    [
      'relationship:12',
      'does not state an effect',
      'Transcriptional regulation: SYN-TF3 (regulator) to SYN-G4 (target); effect unknown; '
      + 'supporting results in view: 1 does not state an effect; 0 distinct publications; synthetic demonstration data.',
    ],
    [
      'relationship:14',
      '2 state activation',
      'Transcriptional regulation: SYN-TF3 (regulator) to SYN-SHARED (target); activates; '
      + 'supporting results in view: 2 state activation; 2 distinct publications; synthetic demonstration data.',
    ],
  ])('%s counts its supporting results by effect: "%s"', (id, words, sentence) => {
    const all = network('network-all-edges')
    const text = edgeDescription(edgeIn(all, id), all, null, vocabularies())
    expect(text).toContain(words)
    expect(text).toBe(sentence)
  })

  it('reads "from" and "to" without role labels, and gives a ref for an entity the graph lacks', () => {
    const all = network('network-all-edges')
    expect(edgeDescription(edgeIn(all, 'relationship:9'), all)).toBe(
      'transcriptional regulation: from SYN-TF2 to SYN-G2; mixed effect; '
      + 'supporting results in view: 1 states activation, 1 states inhibition; 2 distinct publications; synthetic demonstration data.',
    )
    expect(edgeDescription(edgeIn(all, 'relationship:9'), EMPTY_GRAPH)).toMatch(/^transcriptional regulation: from entity:8 to entity:2; /)
    expect(edgeDescription(edgeIn(all, 'relationship:11'), all, presentationAllKeys)).toMatch(
      /^transcriptional regulation: from SYN-TF3 to SYN-G3; /,
    )
  })

  it("names a role-ordered type's roles and states no effect for it; a directed type without polarity is direction only", () => {
    const roles = edgeDescription(roleOrderedEdge, handGraph([roleOrderedEdge]), null, withLigand())
    expect(roles).toMatch(/^Synthetic ligand and receptor: SYN-N0 \(ligand\) to SYN-N1 \(receptor\); /)
    expect(roles).not.toContain('direction only')
    expect(roles).not.toContain('effect')
    expect(edgeDescription(directionOnlyEdge, handGraph([directionOnlyEdge]))).toContain('direction only: this type states no effect')
  })

  it('words the counts the line does not show, the results outside the filters and the publications, and says a label once', () => {
    const all = network('network-all-edges')
    const both = edgeDescription(edgeIn(all, 'relationship:4'), all)
    expect(both).toContain('also 1 result tested and not detected')
    expect(both).not.toContain('supporting result')
    const counted = makeGraphEdge({
      outside_filters: 2,
      distinct: { publications: 2, releases: 1, datasets: 1 },
      outcome_counts: { supported: 1, tested_absent: 0, inconclusive: 3, not_significant: 1 },
    })
    expect(edgeDescription(counted, handGraph([counted]))).toBe(
      'transcriptional regulation: from SYN-N0 to SYN-N1; activates; supporting results in view: 1 states activation; '
      + 'also 3 inconclusive results; also 1 result not significant at the stated threshold; '
      + '2 more results outside the current filters; 2 distinct publications; synthetic demonstration data.',
    )
    const plain = makeGraphEdge({ outside_filters: 1, is_synthetic: false })
    expect(edgeDescription(plain, handGraph([plain]))).toBe(
      'transcriptional regulation: from SYN-N0 to SYN-N1; activates; supporting results in view: 1 states activation; '
      + '1 more result outside the current filters; 1 distinct publication.',
    )
    expect(edgeDescription(edgeIn(all, 'relationship:5'), all, null, vocabularies())).toBe(
      'Transcriptional regulation: SYN-G1 (regulator) to SYN-TF1 (target); inhibits; '
      + 'supporting results in view: 1 states inhibition; 0 distinct publications; synthetic demonstration data.',
    )
  })

  it('never prints "undefined", an object or a dangling " · " for values a deploy can bring early', () => {
    for (const edge of [unknownChannelsEdge, unknownLevelEdge, unresolvedReferenceEdge]) {
      const graph = handGraph([edge])
      for (const vocab of [null, vocabularies()]) {
        for (const text of [edgeLabel(edge, graph, vocab), edgeDescription(edge, graph, null, vocab)]) {
          expect(text, edge.id).not.toContain('undefined')
          expect(text, edge.id).not.toContain('[object')
          expect(text, edge.id).not.toContain('function')
          expect(text, edge.id).not.toMatch(/·\s*($|[;.])/)
        }
      }
    }
    expect(edgeLabel(unknownLevelEdge, handGraph([unknownLevelEdge]))).toBe('syn level')
  })
})

describe('the state and error words', () => {
  function answered(status: number, data: unknown, headers: Record<string, string> = {}): AxiosError {
    return new AxiosError('x', undefined, undefined, undefined, {
      status,
      statusText: '',
      headers,
      config: {} as never,
      data,
    })
  }
  const loaded = 'The network could not be loaded.'
  const reached = 'The network could not be reached.'
  const tooLong = 'The request is too long. Choose fewer entities or filters.'

  it.each<[string, unknown, GraphErrorInfo['kind'], string]>([
    ['a JSON 503', answered(503, { detail: 'SYN-DETAIL' }), 'too_expensive', 'This network is too large to compute. Narrow the seeds, layers or filters.'],
    ['a 429 with Retry-After 7', answered(429, { detail: 'SYN-DETAIL' }, { 'retry-after': '7' }), 'throttled', 'Too many requests. Try again in 7 seconds.'],
    ['a 429 with Retry-After 1', answered(429, { detail: 'SYN-DETAIL' }, { 'retry-after': '1' }), 'throttled', 'Too many requests. Try again in 1 second.'],
    ['a 429 without Retry-After', answered(429, { detail: 'SYN-DETAIL' }), 'throttled', 'Too many requests. Try again shortly.'],
    ['no connection', new AxiosError('SYN-DETAIL', 'ERR_NETWORK'), 'network', reached],
    ['a timeout', new AxiosError('SYN-DETAIL', 'ECONNABORTED'), 'timeout', reached],
    ['a line too long to send', new GraphRequestTooLongError(5000, 4094), 'too_long', tooLong],
    ["a web server's HTML 414", answered(414, '<html>SYN-DETAIL</html>'), 'server_refused', tooLong],
    ["a web server's HTML 400", answered(400, '<html>SYN-DETAIL</html>'), 'server_refused', tooLong],
    ["the API's 400", answered(400, { detail: 'SYN-DETAIL', errors: [] }), 'bad_request', loaded],
    ['a 401', answered(401, { detail: 'SYN-DETAIL' }), 'unauthorized', loaded],
    ["the API's 404", answered(404, { detail: 'SYN-DETAIL' }), 'not_found', loaded],
    ['a 406', answered(406, { detail: 'SYN-DETAIL' }), 'not_acceptable', loaded],
    ['a 200 with no JSON object', new GraphBadBodyError(200), 'bad_body', loaded],
    ['an HTML 500', answered(500, '<html>SYN-DETAIL</html>'), 'http', loaded],
    ['a cancel', new CanceledError(), 'cancelled', loaded],
    ['a plain error', new Error('SYN-DETAIL'), 'unknown', loaded],
  ])('networkErrorText: %s', (_name, error, kind, text) => {
    expect(classifyGraphError(error).kind).toBe(kind)
    expect(networkErrorText(error)).toBe(text)
    expect(networkErrorText(error)).not.toContain('SYN-DETAIL')
  })

  it.each<[string, Parameters<typeof networkStatusText>, string]>([
    ['disabled', ['disabled', { hasGraph: false, empty: true }], 'Choose an entity, and a value for each filter in use, to draw its network.'],
    ['loading', ['loading', { hasGraph: false, empty: true }], 'Loading the network…'],
    ['updating', ['updating', { hasGraph: true, empty: false }], 'Updating the network…'],
    [
      'error without a graph',
      ['error', { error: answered(503, { detail: 'SYN-DETAIL' }), hasGraph: false, empty: true }],
      'This network is too large to compute. Narrow the seeds, layers or filters.',
    ],
    [
      'error with a graph',
      ['error', { error: answered(503, { detail: 'SYN-DETAIL' }), hasGraph: true, empty: false }],
      'This network is too large to compute. Narrow the seeds, layers or filters. Showing the last answer.',
    ],
    ['ready and empty', ['ready', { hasGraph: true, empty: true }], 'No relationships to show.'],
    ['ready', ['ready', { hasGraph: true, empty: false }], ''],
  ])('networkStatusText: %s', (_name, args, text) => {
    expect(networkStatusText(...args)).toBe(text)
  })

  it('plural takes whole phrases and puts the count in the many phrase', () => {
    expect(plural(1, '1 relationship is', '{n} relationships are')).toBe('1 relationship is')
    expect(plural(0, '1 relationship is', '{n} relationships are')).toBe('0 relationships are')
    expect(plural(3, '1 relationship is', '{n} relationships are')).toBe('3 relationships are')
    expect(plural(2, 'one of one', '{n} of {n}')).toBe('2 of 2')
  })
})

describe('what is shown by default and what is hidden', () => {
  it('draws what the request asked for by default', () => {
    expect(defaultShowNonSupporting(network('network-tf1-outcome-all'))).toBe(true)
    expect(defaultShowNonSupporting(network('network-tf1-tested-absent'))).toBe(true)
    expect(defaultShowNonSupporting(network('network-tf1'))).toBe(false)
    expect(defaultShowNonSupporting(null)).toBe(false)
  })

  it('hides edges without support and the entities reached only through them, never a seed', () => {
    const ids = (elements: readonly { id: string }[]) => elements.map((element) => element.id)
    const tfAll = visibleGraph(network('network-tf1-outcome-all'), { showNonSupporting: false })
    expect(ids(tfAll.hiddenEdges)).toEqual(['relationship:7'])
    expect(ids(tfAll.hiddenNodes)).toEqual(['entity:2'])
    expect(ids(tfAll.edges)).toEqual(['relationship:5', 'relationship:6', 'relationship:8'])
    expect(ids(tfAll.nodes)).toEqual(['entity:1', 'entity:4', 'entity:7'])
    const seedOnly = visibleGraph(network('network-tf1-tested-absent'), { showNonSupporting: false })
    expect(ids(seedOnly.hiddenNodes)).toEqual(['entity:2'])
    expect(ids(seedOnly.nodes)).toEqual(['entity:7'])
    const all = visibleGraph(network('network-all-edges'), { showNonSupporting: false })
    expect(ids(all.hiddenEdges)).toEqual(['relationship:7'])
    expect(ids(all.hiddenNodes)).toEqual([])
  })

  it('keeps an entity with no relationship at all, and hides nothing when asked to show everything', () => {
    const graph = handGraph([noCountsEdge])
    const alone = makeNetworkNode({ id: 'entity:950', label: 'SYN-ALONE', is_seed: false })
    const hidden = visibleGraph({ ...graph, nodes: [...graph.nodes, alone] }, { showNonSupporting: false })
    expect(hidden.hiddenNodes.map((node) => node.id)).toEqual(['entity:902'])
    expect(hidden.nodes.map((node) => node.id)).toEqual(['entity:901', 'entity:950'])
    const shown = visibleGraph(network('network-tf1-outcome-all'), { showNonSupporting: true })
    expect([shown.hiddenEdges, shown.hiddenNodes]).toEqual([[], []])
    expect([shown.nodes.length, shown.edges.length]).toEqual([4, 4])
  })
})

describe('presentation', () => {
  it.each(['cose', 'cola', 'concentric', 'circle', 'grid', 'breadthfirst'])('resolveLayout keeps %s', (name) => {
    expect(resolveLayout(name)).toBe(name)
  })

  it.each([null, undefined, '', 'fcose', 'constructor'])('resolveLayout gives cose for %j', (name) => {
    expect(resolveLayout(name)).toBe('cose')
  })

  it.each<[string, GraphPresentation | null | undefined, string]>([
    ['no presentation', undefined, 'SYN-ALPHA'],
    ['a null presentation', null, 'SYN-ALPHA'],
    ['no namespace setting', {}, 'SYN-ALPHA'],
    ['a null namespace', { node_label_namespace: null }, 'SYN-ALPHA'],
    ["the primary identifier's namespace", presentationAllKeys, 'SYN-G3'],
    ['another namespace', { node_label_namespace: 'synthetic_protein' }, 'SYN-ALPHA'],
  ])('nodeLabel: %s', (_name, presentation, label) => {
    const node = nodeIn(network('network-all-edges'), 'entity:3')
    expect([node.label, node.primary_identifier]).toEqual(['SYN-ALPHA', { namespace: 'synthetic_gene', value: 'SYN-G3' }])
    expect(nodeLabel(node, presentation)).toBe(label)
  })

  it('nodeLabel keeps the label without a primary identifier, and for an empty namespace', () => {
    const node = nodeIn(network('network-all-edges'), 'entity:3')
    expect(nodeLabel({ ...node, primary_identifier: null }, presentationAllKeys)).toBe('SYN-ALPHA')
    expect(nodeLabel({ ...node, primary_identifier: { namespace: '', value: 'SYN-EMPTY' } }, { node_label_namespace: '' })).toBe('SYN-ALPHA')
  })

  it.each<[string, GraphPresentation | null | undefined, ReturnType<typeof presentationParams>]>([
    ['an empty presentation', {}, {}],
    ['every key', presentationAllKeys, { layers: ['transcriptional_regulation'], direction: 'both', outcome: ['supported'] }],
    ['no default layers', { default_layers: [] }, {}],
    ['outcome all', { default_filters: { outcome: ['all'] } }, { outcome: 'all' }],
    ['an empty effect list', { default_filters: { effect: [] } }, {}],
    [
      'values outside their closed sets',
      { default_filters: { effect: ['syn_effect'], level: ['direct_interaction', 'x'] } },
      { level: ['direct_interaction'] },
    ],
    ['blank and null layer names', { default_layers: ['', null] }, {}],
    ['a blank beside a layer', { default_layers: ['', 'family:physical'] }, { layers: ['family:physical'] }],
    ['an unknown direction', { default_filters: { direction: 'sideways' } }, {}],
    ['an unknown outcome beside a known one', { default_filters: { outcome: ['supported', 'syn_outcome'] } }, { outcome: ['supported'] }],
    ['only an unknown outcome', { default_filters: { outcome: ['syn_outcome'] } }, {}],
    [
      'an unknown evidence basis beside a known one',
      { default_filters: { evidence_basis: ['experimental', 'syn_basis'] } },
      { evidence_basis: ['experimental'] },
    ],
    ['a known effect beside an unknown one', { default_filters: { effect: ['activating', 'syn_effect'] } }, { effect: ['activating'] }],
    ['a list holding all', { default_filters: { outcome: ['supported', 'all'] } }, { outcome: 'all' }],
    ['null', null, {}],
  ])('presentationParams: %s', (_name, presentation, params) => {
    expect(presentationParams(presentation)).toStrictEqual(params)
  })

  it('keeps its lists between the marker lines, each on one line, as the backend agreement test parses them', () => {
    const lines = presentationSource.split(/\r?\n/)
    const start = '// presentation-lists:start'
    const end = '// presentation-lists:end'
    expect([lines.filter((line) => line === start).length, lines.filter((line) => line === end).length]).toEqual([1, 1])
    expect(lines.indexOf(start)).toBeLessThan(lines.indexOf(end))
    const listLine = /^export const PRESENTATION_([A-Z_]+) = \[(.*)\] as const$/
    const defaultLine = /^export const PRESENTATION_(DEFAULT_[A-Z]+) = '([^']*)'$/
    const quotedNames = /^\s*(?:'[^']*'(?:\s*,\s*'[^']*')*\s*,?)?\s*$/
    const lists: Record<string, string[]> = {}
    const defaults: Record<string, string> = {}
    for (const line of lines.slice(lines.indexOf(start) + 1, lines.indexOf(end))) {
      const list = listLine.exec(line)
      if (list !== null) {
        expect(list[2], line).toMatch(quotedNames)
        lists[list[1]] = Array.from(list[2].matchAll(/'([^']*)'/g), (match) => match[1])
      }
      const value = defaultLine.exec(line)
      if (value !== null) defaults[value[1]] = value[2]
    }
    expect(lists).toEqual({
      KEYS: [...PRESENTATION_KEYS],
      FILTER_PANELS: [...PRESENTATION_FILTER_PANELS],
      EVIDENCE_PANELS: [...PRESENTATION_EVIDENCE_PANELS],
      LAYOUTS: [...PRESENTATION_LAYOUTS],
      ACCENTS: [...PRESENTATION_ACCENTS],
      DEFAULT_FILTER_NAMES: [...PRESENTATION_DEFAULT_FILTER_NAMES],
    })
    expect(defaults).toEqual({ DEFAULT_LAYOUT: PRESENTATION_DEFAULT_LAYOUT, DEFAULT_ACCENT: PRESENTATION_DEFAULT_ACCENT })
    expect(lists.LAYOUTS).toEqual(['cose', 'cola', 'concentric', 'circle', 'grid', 'breadthfirst'])
    expect(defaults).toEqual({ DEFAULT_LAYOUT: 'cose', DEFAULT_ACCENT: 'blue' })
  })
})

describe('layout options', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  const frame = { fit: true, padding: 30 }
  const coseReduced = {
    name: 'cose', animate: true, randomize: false, nodeDimensionsIncludeLabels: true,
    animationThreshold: Number.POSITIVE_INFINITY, ...frame,
  }

  it.each<[TypedLayoutName, boolean, Record<string, unknown>]>([
    ['cose', false, { name: 'cose', animate: true, randomize: false, nodeDimensionsIncludeLabels: true, ...frame }],
    ['cose', true, coseReduced],
    [
      'cola',
      false,
      { name: 'cola', animate: true, randomize: false, maxSimulationTime: 2000, nodeSpacing: 20, edgeLength: 90, ...frame },
    ],
    ['cola', true, coseReduced],
    ['concentric', false, { name: 'concentric', animate: true, ...frame }],
    ['concentric', true, { name: 'concentric', animate: false, ...frame }],
    ['circle', false, { name: 'circle', animate: true, ...frame }],
    ['circle', true, { name: 'circle', animate: false, ...frame }],
    ['grid', false, { name: 'grid', animate: true, ...frame }],
    ['grid', true, { name: 'grid', animate: false, ...frame }],
    ['breadthfirst', false, { name: 'breadthfirst', animate: true, ...frame }],
    ['breadthfirst', true, { name: 'breadthfirst', animate: false, ...frame }],
  ])('%s, reduced motion %s', (name, reducedMotion, options) => {
    expect(layoutOptions(name, { reducedMotion })).toStrictEqual(options)
  })

  it('prefersReducedMotion is false without matchMedia, as in jsdom', () => {
    expect(typeof window.matchMedia).toBe('undefined')
    expect(prefersReducedMotion()).toBe(false)
  })

  it('prefersReducedMotion reads the reduce query', () => {
    vi.stubGlobal('matchMedia', (query: string) => ({ matches: query === '(prefers-reduced-motion: reduce)', media: query }))
    expect(prefersReducedMotion()).toBe(true)
    vi.stubGlobal('matchMedia', (query: string) => ({ matches: false, media: query }))
    expect(prefersReducedMotion()).toBe(false)
  })
})
