import type { ElementDefinition } from 'cytoscape'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import * as extras from '../../../mocks/fixtures/typedGraph/extras'
import { recorded } from '../../../test/graphHooks'
import type { GraphEdge, GraphNetworkResponse, GraphNode, GraphPresentation, GraphVocabularies } from '../types'
import { EMPTY_GRAPH, graphOfNetwork } from './graph'
import type { TypedGraph } from './graph'
import { buildElements, layoutKey } from './typedElements'
import type { BuiltElements } from './typedElements'

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

function body(name: string): GraphNetworkResponse {
  return recorded<GraphNetworkResponse>(name).body
}

function network(name: string): TypedGraph {
  return graphOfNetwork(body(name))
}

function edgeIn(graph: TypedGraph, id: string): GraphEdge {
  const edge = graph.edges.find((candidate) => candidate.id === id)
  if (edge === undefined) throw new Error(`no ${id} in the graph`)
  return edge
}

function elementOf(built: BuiltElements, id: string): ElementDefinition {
  const element = built.elements.find((candidate) => candidate.data.id === id)
  if (element === undefined) throw new Error(`no element ${id}`)
  return element
}

function classesOf(built: BuiltElements, id: string): string {
  const { classes } = elementOf(built, id)
  expect(typeof classes, id).toBe('string')
  return String(classes)
}

/** Whole class names, so `is-selected` never matches inside another name. */
function idsWith(built: BuiltElements, name: string): string[] {
  return built.elements.filter((element) => String(element.classes).split(' ').includes(name)).map((element) => element.data.id)
}

describe('buildElements', () => {
  it('uses the refs verbatim as ids, nodes first, with primitive data, no position and the records in its maps', () => {
    const response = body('network-all-edges')
    const built = buildElements(graphOfNetwork(response), { showNonSupporting: true })
    expect(built.elements.map((element) => element.data.id)).toEqual([
      ...response.nodes.map((node) => node.id),
      ...response.edges.map((edge) => edge.id),
    ])
    for (const element of built.elements) {
      expect(element.position, element.data.id).toBeUndefined()
      for (const value of Object.values(element.data)) expect(['string', 'number'], element.data.id).toContain(typeof value)
    }
    for (const node of response.nodes) {
      expect(elementOf(built, node.id).data).toStrictEqual({ id: node.id, label: node.label, type: node.type })
      expect(built.nodes.get(node.id)).toBe(node)
    }
    for (const edge of response.edges) {
      const { data } = elementOf(built, edge.id)
      expect(Object.keys(data).sort(), edge.id).toEqual(['id', 'label', 'publications', 'source', 'target'])
      expect([data.source, data.target, data.publications], edge.id).toEqual([edge.source, edge.target, edge.distinct.publications])
      expect(built.edges.get(edge.id)).toBe(edge)
    }
    expect([built.nodes.size, built.edges.size]).toEqual([12, 12])
    expect(elementOf(built, 'relationship:5').data.label).toBe('inhibits')
    expect(elementOf(built, 'relationship:3').data.label).toBe('direct interaction')
    expect(elementOf(built, 'relationship:9').data.publications).toBe(2)
  })

  it('keeps refs of another kind verbatim', () => {
    const graph = graphOfNetwork(extras.makeNetworkResponse({ nodes: extras.ppiNodes, edges: [extras.ppiEdge] }))
    const built = buildElements(graph, { showNonSupporting: false })
    expect(built.elements.map((element) => element.data.id)).toEqual(['ppi-protein:1', 'ppi-protein:2', 'ppi-interaction:42'])
    expect(elementOf(built, 'ppi-interaction:42').data).toMatchObject({ source: 'ppi-protein:1', target: 'ppi-protein:2' })
    expect(built.edges.get('ppi-interaction:42')).toBe(extras.ppiEdge)
    expect(built.nodes.get('ppi-protein:1')).toBe(extras.ppiNodes[0])
  })

  it('makes relationships 5 and 6 two elements with swapped ends', () => {
    const built = buildElements(network('network-tf1'), { showNonSupporting: false })
    const ends = (id: string) => {
      const { data } = elementOf(built, id)
      return [data.source, data.target]
    }
    expect(ends('relationship:5')).toEqual(['entity:1', 'entity:7'])
    expect(ends('relationship:6')).toEqual(['entity:7', 'entity:1'])
    expect(built.elements.filter((element) => element.data.id === 'relationship:5' || element.data.id === 'relationship:6')).toHaveLength(2)
  })

  it('gives each element its channel classes in order, then hidden, then is-selected', () => {
    const built = buildElements(network('network-tf1-outcome-all'), { showNonSupporting: false, selectedId: 'relationship:7' })
    expect(classesOf(built, 'relationship:7')).toBe(
      'rt-transcriptional_regulation fam-regulation directed out-tested-absent synthetic mk-none ln-tested-absent hidden is-selected',
    )
    expect(classesOf(built, 'relationship:5')).toBe(
      'rt-transcriptional_regulation fam-regulation directed eff-inhibiting synthetic mk-tee ln-supported',
    )
    expect(classesOf(built, 'entity:2')).toBe('et-gene sh-gene hidden')
    expect(classesOf(built, 'entity:7')).toBe('et-gene sh-gene seed')
  })

  it('hides what visibleGraph hides, never a seed, and nothing when everything is shown', () => {
    const hidden = (name: string, showNonSupporting: boolean) => idsWith(buildElements(network(name), { showNonSupporting }), 'hidden').sort()
    expect(hidden('network-tf1-outcome-all', false)).toEqual(['entity:2', 'relationship:7'])
    expect(hidden('network-tf1-tested-absent', false)).toEqual(['entity:2', 'relationship:7'])
    expect(hidden('network-p2-tested-absent', false)).toEqual(['entity:13', 'relationship:4'])
    expect(hidden('network-all-edges', false)).toEqual(['relationship:7'])
    expect(hidden('network-tf1-outcome-all', true)).toEqual([])
    expect(hidden('network-all-edges', true)).toEqual([])
  })

  it('marks only the selected id as selected', () => {
    const graph = network('network-tf1-outcome-all')
    const selected = (selectedId?: string | null) => idsWith(buildElements(graph, { showNonSupporting: true, selectedId }), 'is-selected')
    expect(selected('entity:7')).toEqual(['entity:7'])
    expect(classesOf(buildElements(graph, { showNonSupporting: true, selectedId: 'entity:7' }), 'entity:7')).toBe('et-gene sh-gene seed is-selected')
    expect(selected('relationship:6')).toEqual(['relationship:6'])
    expect(selected(null)).toEqual([])
    expect(selected(undefined)).toEqual([])
    expect(selected('entity:999')).toEqual([])
  })

  it.each<[string, string, string, GraphPresentation | undefined]>([
    ['no presentation', 'entity:3', 'SYN-ALPHA', undefined],
    ["presentationAllKeys, the genes' primary namespace", 'entity:3', 'SYN-G3', extras.presentationAllKeys],
    ["presentationAllKeys, the genes' primary namespace", 'entity:6', 'SYN-G6', extras.presentationAllKeys],
    ['presentationAllKeys, which a protein does not match', 'entity:13', 'SYN-SHARED', extras.presentationAllKeys],
    ["the proteins' primary namespace", 'entity:13', 'SYN-P3', { node_label_namespace: 'synthetic_protein' }],
    ["the proteins' primary namespace, which a gene does not match", 'entity:3', 'SYN-ALPHA', { node_label_namespace: 'synthetic_protein' }],
  ])('labels nodes through the presentation: %s gives %s %s', (_name, id, label, presentation) => {
    const built = buildElements(network('network-all-edges'), { showNonSupporting: true, presentation })
    expect(elementOf(built, id).data.label).toBe(label)
  })

  it('labels edges through the vocabularies', () => {
    const graph = graphOfNetwork(extras.makeNetworkResponse({ edges: [extras.unspecifiedEdge] }))
    const vocabularies = recorded<GraphVocabularies>('vocabularies').body
    const label = (vocab: GraphVocabularies | null) =>
      elementOf(buildElements(graph, { showNonSupporting: true, vocabularies: vocab }), 'relationship:910').data.label
    expect(label(vocabularies)).toBe('Protein interaction, type not stated')
    expect(label(null)).toBe('protein interaction unspecified')
  })
})

describe('layoutKey', () => {
  it('is JSON of the layout name and the ids, each list sorted in plain string order', () => {
    expect(JSON.parse(layoutKey(network('network-all-edges'), 'cose'))).toEqual([
      'cose',
      ['entity:1', 'entity:10', 'entity:12', 'entity:13', 'entity:2', 'entity:3', 'entity:4', 'entity:5', 'entity:6', 'entity:7', 'entity:8', 'entity:9'],
      [
        'relationship:10', 'relationship:11', 'relationship:12', 'relationship:13', 'relationship:14', 'relationship:3',
        'relationship:4', 'relationship:5', 'relationship:6', 'relationship:7', 'relationship:8', 'relationship:9',
      ],
    ])
  })

  it('stays the same when the data change and the ids do not', () => {
    const tf1 = network('network-tf1')
    const v1 = network('network-tf1-v1')
    expect([edgeIn(tf1, 'relationship:6').effect, edgeIn(v1, 'relationship:6').effect]).toEqual(['inhibiting', 'activating'])
    expect(layoutKey(v1, 'cose')).toBe(layoutKey(tf1, 'cose'))
  })

  it('counts the elements that hiding removes from view, so a visibility change keeps it', () => {
    const graph = network('network-tf1-outcome-all')
    const ids = (showNonSupporting: boolean) => buildElements(graph, { showNonSupporting }).elements.map((element) => element.data.id)
    expect(ids(false)).toEqual(ids(true))
    expect(layoutKey(graph, 'cose')).toBe(
      JSON.stringify([
        'cose',
        ['entity:1', 'entity:2', 'entity:4', 'entity:7'],
        ['relationship:5', 'relationship:6', 'relationship:7', 'relationship:8'],
      ]),
    )
  })

  it('changes with new ids and with the layout name', () => {
    const tf1 = network('network-tf1')
    expect(layoutKey(network('network-tf1-outcome-all'), 'cose')).not.toBe(layoutKey(tf1, 'cose'))
    expect(layoutKey(tf1, 'cola')).not.toBe(layoutKey(tf1, 'cose'))
  })

  it('cannot confuse opaque ids that hold a separator', () => {
    const node = (id: string) => extras.makeNetworkNode({ id, label: 'SYN-N' })
    const one: TypedGraph = { ...EMPTY_GRAPH, nodes: [node('entity:1,entity:2')] }
    const two: TypedGraph = { ...EMPTY_GRAPH, nodes: [node('entity:1'), node('entity:2')] }
    const asEdge: TypedGraph = {
      ...EMPTY_GRAPH,
      nodes: [node('entity:1')],
      edges: [extras.makeGraphEdge({ id: 'entity:2', source: 'entity:1', target: 'entity:1' })],
    }
    expect(JSON.parse(layoutKey(one, 'cose'))).toEqual(['cose', ['entity:1,entity:2'], []])
    expect(new Set([layoutKey(one, 'cose'), layoutKey(two, 'cose'), layoutKey(asEdge, 'cose')]).size).toBe(3)
  })
})

describe('the hand-written shapes', () => {
  it('are synthetic: every node has a SYN- label and a synthetic primary namespace, or no primary identifier', () => {
    const nodes: GraphNode[] = [extras.makeNetworkNode(), ...extras.makeNetworkResponse().nodes, extras.makeGraphNode()]
    const visit = (value: unknown) => {
      if (Array.isArray(value)) {
        value.forEach(visit)
      } else if (typeof value === 'object' && value !== null) {
        if ('primary_identifier' in value && 'label' in value) nodes.push(value as GraphNode)
        if ('nodes' in value) visit(value.nodes)
      }
    }
    Object.values(extras).forEach(visit)
    expect(nodes.map((node) => node.id)).toEqual(
      expect.arrayContaining(['entity:901', 'entity:902', 'ppi-protein:1', 'ppi-protein:2', 'entity:900']),
    )
    for (const node of nodes) {
      expect(node.label, node.id).toMatch(/^SYN-/)
      if (node.primary_identifier !== null) {
        expect(['synthetic_gene', 'synthetic_protein'], node.id).toContain(node.primary_identifier.namespace)
        expect(node.primary_identifier.value, node.id).toMatch(/^SYN-/)
      }
    }
  })
})
