import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import cytoscape from 'cytoscape'
import type { Core, ElementDefinition, StylesheetJsonBlock } from 'cytoscape'
import { http, HttpResponse } from 'msw'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  closureDropNetwork, derivedSeedNetwork, isoformRelease, makeGraphNode, makeNetworkNode, mappingRelease, methodFilterNetwork,
  sharedEntryNetwork,
} from '../../../mocks/fixtures/typedGraph/extras'
import { server } from '../../../mocks/server'
import { useAuthStore } from '../../../store/authStore'
import { GRAPH_WAIT, graphWrapper, newGraphClient, recordRequests, recorded } from '../../../test/graphHooks'
import type { RequestLog } from '../../../test/graphHooks'
import { recordAnswers } from '../../../test/graphRoutes'
import { legendEntries } from '../legend/legendEntries'
import { LEGEND_TEXT } from '../legend/legendText'
import { TypedLegend } from '../legend/TypedLegend'
import { EntityPanel } from '../panels/EntityPanel'
import { joinParamsOf } from '../panels/panelParams'
import { derivationTexts } from '../panels/panelWords'
import { TypedSelectionPanel } from '../panels/TypedSelectionPanel'
import { edgeRows, entityRows } from '../tables/edgeRows'
import { entitySuffix } from '../tables/tableWords'
import type { GraphEdge, GraphEvidenceResponse, GraphNetworkQuery, GraphNetworkResponse, GraphVocabularies } from '../types'
import { isolateText } from '../words/isolateText'
import * as derivedWords from './derivedWords'
import { graphOfNetwork, isDerivedSeed, keptGraph, nodeDerivations } from './graph'
import type { TypedGraph, TypedGraphNode } from './graph'
import { noticeLines } from './notice'
import { DERIVED_RING_DASH, LABEL_OUTLINE, LABEL_TEXT, NODE_FILL, PAGE_BACKGROUNDS } from './palette'
import { buildElements, layoutKey } from './typedElements'
import { TypedNetworkNotice } from './TypedNetworkNotice'
import { TYPED_STYLESHEET } from './typedStyles'
import { visibleGraph } from './visibility'

// X1 spec 12.4 (XD): 12.0 item 1's hooks with the 'HTTP 501' text check, no scrollTo spy.
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
  // F3's panels print a status, so an answer from MSW's miss would show here before the page goes.
  expect(document.body.textContent ?? '').not.toContain('HTTP 501')
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

// Words copied from the spec's section 9.3 and F3's provisional sentence (G/panels/EntityPanel.test.tsx:46), never from the modules under test.
const DERIVED_LEGEND = 'Dashed two-tone outline: an entity reached by following gene-to-protein links from a starting entity (a derived seed). The line under its name gives the type of each link followed.'
const PROVISIONAL = "Status: provisional (created from a release's evidence; no reference release has stated this identifier yet)."
const LOADING_ENTITY = 'Loading this entity…'
const NOT_LISTED = 'Under the current filters a network would not list this relationship: none of its results in view has a requested outcome.'
const ENCODES_ROW = 'Encodes: SYN-TF1 (Gene) to SYN-P1 (Protein (UniProtKB entry))'
/** 9.3's derivationText, copied. */
const derivationText = (seedName: string, via: string, releaseNames: readonly string[]) => {
  const from = `Reached from ${isolateText(seedName)} ${via}`
  return releaseNames.length === 0 ? `${from}.` : `${from}; stated by ${releaseNames.map((name) => isolateText(name)).join(', ')}.`
}
const ENTITY_7 = '/api/graph/v1/entities/entity%3A7'
const ENTITY_11 = '/api/graph/v1/entities/entity%3A11'
const RELATIONSHIP_1 = '/api/graph/v1/relationships/relationship%3A1'
const EVIDENCE_1 = '/api/graph/v1/relationships/relationship%3A1/evidence'
const BANNED = /does not exist|not found|undefined|HTTP|No page uses|\b[Nn]one\b/

const vocab = recorded<GraphVocabularies>('vocabularies').body
const net = (name: string): TypedGraph => graphOfNetwork(recorded<GraphNetworkResponse>(name).body)
const tf1 = net('network-tf1-expand')
const tf1Expand = () => recorded<GraphNetworkResponse>('network-tf1-expand').body
/** The recorded expanded SYN-TF1 answer under another echo (12.0). */
const echoed = (changes: Partial<GraphNetworkQuery>): GraphNetworkResponse => ({ ...tf1Expand(), query: { ...tf1Expand().query, ...changes } })
const nodeOf = (graph: TypedGraph, id: string) => graph.nodes.find((node) => node.id === id) as TypedGraphNode
const edgeOf = (graph: TypedGraph, id: string) => graph.edges.find((edge) => edge.id === id) as GraphEdge
const asked = () => requests.urls.map((url) => url.pathname + url.search)
const copy = <T,>(value: T): T => JSON.parse(JSON.stringify(value)) as T

// G/network/typedStyles.test.ts:37-68, 74-81, 124-133, restated.
function channel(value: number): number {
  const c = value / 255
  return c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4)
}
function rgbParts(hex: string): number[] {
  const digits = hex.replace('#', '')
  return [0, 2, 4].map((start) => parseInt(digits.slice(start, start + 2), 16))
}
function relativeLuminance(hex: string): number {
  const [r, g, b] = rgbParts(hex)
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)
}
function contrastRatio(a: string, b: string): number {
  const [lighter, darker] = [relativeLuminance(a), relativeLuminance(b)].sort((x, y) => y - x)
  return (lighter + 0.05) / (darker + 0.05)
}
function rgb(hex: string): string {
  return `rgb(${rgbParts(hex).join(',')})`
}
function withCy(elements: ElementDefinition[], check: (cy: Core) => void): void {
  const cy = cytoscape({ headless: true, styleEnabled: true, elements, style: TYPED_STYLESHEET })
  try {
    check(cy)
  } finally {
    cy.destroy()
  }
}
function styleOf(block: StylesheetJsonBlock): Record<string, unknown> {
  return Object.fromEntries(Object.entries('style' in block ? block.style : block.css))
}
function blockStyle(selector: string): Record<string, unknown> {
  const blocks = TYPED_STYLESHEET.filter((block) => block.selector === selector)
  expect(blocks, selector).toHaveLength(1)
  return styleOf(blocks[0])
}

const build = (graph: TypedGraph, showNonSupporting = false) => buildElements(keptGraph(graph).graph, { showNonSupporting, vocabularies: vocab }).elements
const nodeTriples = (elements: ElementDefinition[]) =>
  elements.filter((element) => element.data.source === undefined).map((element) => [element.data.id, element.classes, element.data.label])
const labelOf = (elements: ElementDefinition[], id: string) => elements.find((element) => element.data.id === id)?.data.label

describe('derived seeds and joins on F2 and F3', () => {
  it('XD-1: a derived seed is a node with a well-formed path that the caller did not name', () => {
    for (const node of [makeNetworkNode(), makeGraphNode(), null, undefined]) expect(nodeDerivations(node)).toEqual([])
    const a = { seed: 'entity:7', via: ['relationship:1'] }
    const b = { seed: 'entity:7', via: ['relationship:1', 'relationship:2'] }
    const bent = makeNetworkNode({
      derived_from: [a, { seed: 7, via: ['relationship:1'] }, { seed: 'entity:7', via: [] }, { seed: 'entity:7', via: [1] }, null, 'x', b] as never,
    })
    expect(nodeDerivations(bent)).toEqual([a, b])
    expect(isDerivedSeed(nodeOf(tf1, 'entity:10'))).toBe(true)
    expect(isDerivedSeed(nodeOf(tf1, 'entity:11'))).toBe(true)
    for (const id of ['entity:7', 'entity:1', 'entity:12']) expect(isDerivedSeed(nodeOf(tf1, id)), id).toBe(false)
    expect(isDerivedSeed(makeNetworkNode({ is_seed: true, derived_from: [a] }))).toBe(false)
  })

  it("XD-2: each path's types and stating releases come from the graph", () => {
    const paths = (graph: TypedGraph, id: string | null, vocabularies: GraphVocabularies | null = vocab) =>
      derivedWords.derivedPaths(id === null ? null : nodeOf(graph, id), graph, vocabularies)
    expect(tf1.releases.map((release) => release.id)).toEqual(['release:2'])
    expect(paths(tf1, 'entity:10')).toEqual([{ seed: 'entity:7', types: ['Encodes'], releases: [tf1.releases[0]] }])
    expect(paths(tf1, 'entity:11')).toEqual([{ seed: 'entity:7', types: ['Encodes', 'Isoform of'], releases: [tf1.releases[0]] }])
    for (const id of ['entity:1', 'entity:4', 'entity:7', 'entity:12', null]) expect(paths(tf1, id), String(id)).toEqual([])
    const cut = { ...tf1, edges: tf1.edges.filter((edge) => edge.id !== 'relationship:2') }
    expect(paths(cut, 'entity:11')).toEqual([{ seed: 'entity:7', types: ['Encodes', 'a reference link'], releases: [tf1.releases[0]] }])
    expect(paths(tf1, 'entity:10', null)[0].types).toEqual(['encodes'])
    expect(paths(net('network-p1-isoform-expand'), 'entity:7')[0].types).toEqual(['Isoform of', 'Encodes'])
    const shared = graphOfNetwork(sharedEntryNetwork)
    expect(paths(shared, 'entity:986')).toEqual([{ seed: 'entity:985', types: ['Encodes'], releases: [mappingRelease] }])
    expect(paths(shared, 'entity:989')).toEqual([{ seed: 'entity:985', types: ['Isoform of'], releases: [isoformRelease] }])
  })

  it('XD-3: the words of derivedWords.ts, outside LEGEND_TEXT', () => {
    expect(derivedWords.UNLISTED_STEP).toBe('a reference link')
    expect(derivedWords.DERIVED_LEGEND).toBe(DERIVED_LEGEND)
    const path = (types: string[]) => ({ seed: 'entity:7', types, releases: [] })
    const encodes = [path(['Encodes'])]
    const outputs: [string, string][] = [
      [derivedWords.pathViaText(['Encodes']), 'via Encodes'],
      [derivedWords.pathViaText(['Encodes', 'Isoform of']), 'via Encodes, then Isoform of'],
      [derivedWords.viaText([path(['Encodes']), path(['Isoform of'])]), 'via Encodes and via Isoform of'],
      [derivedWords.viaText([path(['Encodes']), path(['Encodes'])]), 'via Encodes'],
      [derivedWords.viaText([]), ''],
      [derivedWords.derivedCanvasLabel('SYN-P1', encodes), 'SYN-P1\nvia Encodes'],
      [derivedWords.derivedCanvasLabel('SYN-P1', []), 'SYN-P1'],
      [derivedWords.derivedCanvasLabel('SYN-X\nvia Encodes', [path(['Isoform of'])]), 'SYN-X via Encodes\nvia Isoform of'],
      [derivedWords.derivedCanvasLabel('A\r\nB', encodes), 'A  B\nvia Encodes'],
      [derivedWords.derivedCanvasLabel('SYN-X\nvia Encodes', []), 'SYN-X\nvia Encodes'],
      [derivedWords.derivedRowSuffix(encodes), ', reached via Encodes'],
      [derivedWords.derivedRowSuffix([]), ''],
      [derivedWords.derivationText('SYN-TF1 (Gene)', 'via Encodes, then Isoform of', ['Synthetic regulation example v2']),
        `Reached from ${isolateText('SYN-TF1 (Gene)')} via Encodes, then Isoform of; stated by ${isolateText('Synthetic regulation example v2')}.`],
      [derivedWords.derivationText('SYN-TF1 (Gene)', 'via Encodes', []), `Reached from ${isolateText('SYN-TF1 (Gene)')} via Encodes.`],
      [derivedWords.derivationText('SYN-TF1 (Gene)', 'via Encodes', ['Synthetic extras mapping x1', 'Synthetic extras isoforms x1']),
        `Reached from ${isolateText('SYN-TF1 (Gene)')} via Encodes; stated by ${isolateText('Synthetic extras mapping x1')}, ${isolateText('Synthetic extras isoforms x1')}.`],
      [derivedWords.derivedSeedsLeftText(6, 26),
        'At least 6 entities reached through gene-to-protein links were left out: a network looks up at most 26 entities, the chosen and the reached together.'],
      [derivedWords.derivedSeedsLeftText(1, 26),
        'At least 1 entity reached through gene-to-protein links was left out: a network looks up at most 26 entities, the chosen and the reached together.'],
      [derivedWords.derivedSeedsLeftText(1, 1),
        'At least 1 entity reached through gene-to-protein links was left out: a network looks up at most 1 entity, the chosen and the reached together.'],
      ...[null, 0, 1.5].map((max): [string, string] => [derivedWords.derivedSeedsLeftText(2, max),
        'At least 2 entities reached through gene-to-protein links were left out to keep the network within its limits.']),
    ]
    for (const [got, want] of outputs) expect(got).toBe(want)
    const strings = [...(Object.values(derivedWords) as unknown[]).filter((value): value is string => typeof value === 'string'), ...outputs.map(([got]) => got)]
    expect(strings.filter((value) => ['undefined', '[object', 'not enabled', 'mapping release', 'UniProt release'].some((word) => value.includes(word))
      || BANNED.test(value))).toEqual([])
    expect(Object.values(LEGEND_TEXT)).not.toContain(DERIVED_LEGEND)
  })

  it("XD-4: a join's panel takes the network's releases and no filter", () => {
    const join = edgeOf(tf1, 'relationship:1')
    const query = tf1.query as GraphNetworkQuery
    expect(joinParamsOf(join, query)).toEqual({})
    expect(joinParamsOf(join, { ...query, releases: ['release:1'] })).toEqual({ releases: ['release:1'] })
    expect(joinParamsOf(join, { ...query, browser: 'synthetic-regulation' })).toEqual({ browser: 'synthetic-regulation' })
    expect(joinParamsOf(join, { ...query, evidence_basis: ['experimental'], method: ['MI:0018'], outcome: ['all'] })).toEqual({})
    expect(joinParamsOf(edgeOf(tf1, 'relationship:3'), query)).toBeNull()
    expect(joinParamsOf(join, { ...query, expand: null })).toBeNull()
    for (const [edge, echo] of [[null, query], [undefined, query], [join, null], [join, undefined]] as const) expect(joinParamsOf(edge, echo)).toBeNull()
  })

  it("XD-5: the entity panel's path sentences name the seed, the links and the releases", () => {
    expect(derivationTexts('entity:10', tf1, null, vocab)).toEqual([derivationText('SYN-TF1 (Gene)', 'via Encodes', ['Synthetic regulation example v2'])])
    expect(derivationTexts('entity:11', tf1, null, vocab)).toEqual([derivationText('SYN-TF1 (Gene)', 'via Encodes, then Isoform of', ['Synthetic regulation example v2'])])
    expect(derivationTexts('entity:7', tf1, null, vocab)).toEqual([])
    expect(derivationTexts('entity:12', tf1, null, vocab)).toEqual([])
    expect(derivationTexts('entity:10', null, null, vocab)).toEqual([])
    expect(derivationTexts('entity:10', net('network-tf1-expand-v1'), null, vocab))
      .toEqual([derivationText('SYN-TF1 (Gene)', 'via Encodes', ['Synthetic regulation example v1 (superseded)'])])
    expect(derivationTexts('entity:7', net('network-p1-expand'), null, vocab))
      .toEqual([derivationText('SYN-P1 (Protein (UniProtKB entry))', 'via Encodes', ['Synthetic regulation example v2'])])
    const shared = graphOfNetwork(sharedEntryNetwork)
    expect(derivationTexts('entity:986', shared, null, vocab))
      .toEqual([derivationText('SYN-N985 (Protein (UniProtKB entry))', 'via Encodes', ['Synthetic extras mapping x1'])])
    expect(derivationTexts('entity:989', shared, null, vocab))
      .toEqual([derivationText('SYN-N985 (Protein (UniProtKB entry))', 'via Isoform of', ['Synthetic extras isoforms x1'])])
    const body = copy(tf1Expand())
    const ten = body.nodes.find((node) => node.id === 'entity:10') as TypedGraphNode
    const far = `entity:${'9'.repeat(1200)}`
    expect(far).toHaveLength(1207)
    ten.derived_from = [{ ...(ten.derived_from ?? [])[0], seed: far }]
    const texts = derivationTexts('entity:10', graphOfNetwork(body), null, vocab)
    expect(texts).toEqual([derivationText(`entity:${'9'.repeat(993)}… (207 more characters not shown)`, 'via Encodes', ['Synthetic regulation example v2'])])
    expect(texts[0]).toHaveLength(1106)
  })

  it('XD-6: derived seeds take their class and a second label line, and the joins their labels', () => {
    const elements = build(tf1)
    expect(nodeTriples(elements)).toEqual([
      ['entity:1', 'et-gene sh-gene', 'SYN-G1'],
      ['entity:4', 'et-gene sh-gene', 'SYN-G4'],
      ['entity:7', 'et-gene sh-gene seed', 'SYN-TF1'],
      ['entity:10', 'et-protein sh-protein derived', 'SYN-P1\nvia Encodes'],
      ['entity:11', 'et-protein_isoform sh-isoform derived', 'SYN-P1-1\nvia Encodes, then Isoform of'],
      ['entity:12', 'et-protein sh-protein', 'SYN-P2'],
    ])
    expect(labelOf(elements, 'relationship:1')).toBe('Encodes · Synthetic regulation example v2')
    expect(labelOf(elements, 'relationship:2')).toBe('Isoform of · Synthetic regulation example v2')
    for (const element of elements) {
      for (const value of Object.values(element.data)) expect(['string', 'number'], String(value)).toContain(typeof value)
    }
    const v1 = build(net('network-tf1-expand-v1'))
    expect([labelOf(v1, 'relationship:1'), labelOf(v1, 'relationship:2')])
      .toEqual(['Encodes · Synthetic regulation example v1 (superseded)', 'Isoform of · Synthetic regulation example v1 (superseded)'])
    const p1 = build(net('network-p1-expand'))
    expect([labelOf(p1, 'entity:7'), labelOf(p1, 'entity:11')]).toEqual(['SYN-TF1\nvia Encodes', 'SYN-P1-1\nvia Isoform of'])
    const isoform = build(net('network-p1-isoform-expand'))
    expect([labelOf(isoform, 'entity:10'), labelOf(isoform, 'entity:7')]).toEqual(['SYN-P1\nvia Isoform of', 'SYN-TF1\nvia Isoform of, then Encodes'])
    const p2 = build(net('network-p2-expand'))
    expect(p2.filter((element) => (element.classes ?? '').toString().split(' ').includes('derived'))).toEqual([])
    const plain = { ...keptGraph(tf1).graph, nodes: keptGraph(tf1).graph.nodes.map((node) => {
      const rest = { ...node }
      delete rest.derived_from
      return rest
    }) }
    expect(layoutKey(keptGraph(tf1).graph, 'cose')).toBe(layoutKey(plain, 'cose'))
  })

  it('XD-7: the dashed ring, its contrast, and no Cytoscape warning', () => {
    withCy(build(tf1), (cy) => {
      const derived = cy.$id('entity:10')
      expect(['border-style', 'outline-style', 'border-color', 'outline-color', 'border-width', 'outline-width', 'text-wrap'].map((name) => derived.style(name)))
        .toEqual(['dashed', 'dashed', rgb(LABEL_OUTLINE), rgb(LABEL_TEXT), '3px', '2px', 'wrap'])
      expect([cy.$id('entity:7').style('border-style'), cy.$id('entity:7').style('outline-style')]).toEqual(['solid', 'solid'])
      expect([cy.$id('entity:1').style('border-width'), cy.$id('entity:1').style('outline-width')]).toEqual(['0px', '0px'])
    })
    expect(blockStyle('node.derived')).toEqual({
      'border-width': 3, 'border-style': 'dashed', 'border-dash-pattern': [4, 2], 'border-color': LABEL_OUTLINE, 'outline-width': 2,
      'outline-style': 'dashed', 'outline-color': LABEL_TEXT, 'outline-offset': 0, 'text-wrap': 'wrap',
    })
    expect(DERIVED_RING_DASH).toEqual([4, 2])
    const at = TYPED_STYLESHEET.findIndex((block) => block.selector === 'node.derived')
    expect(at).toBe(TYPED_STYLESHEET.findIndex((block) => block.selector === 'node.seed') + 1)
    TYPED_STYLESHEET.forEach((block, index) => {
      if (String(block.selector).startsWith('edge')) expect(at, String(block.selector)).toBeLessThan(index)
    })
    expect(contrastRatio(LABEL_OUTLINE, NODE_FILL)).toBeGreaterThanOrEqual(3)
    expect(contrastRatio(LABEL_TEXT, NODE_FILL)).toBeGreaterThanOrEqual(3)
    for (const background of [PAGE_BACKGROUNDS.light, PAGE_BACKGROUNDS.dark]) {
      expect(Math.max(contrastRatio(LABEL_OUTLINE, background), contrastRatio(LABEL_TEXT, background)), background).toBeGreaterThanOrEqual(3)
    }
    const expanded = ['network-tf1-expand', 'network-tf1-expand-outcome-all', 'network-tf1-expand-v1', 'network-p1-expand', 'network-p1-isoform-expand',
      'network-p2-expand', 'network-tf1-expand-visitor']
    for (const name of expanded) {
      for (const show of [false, true]) withCy(build(net(name), show), (cy) => expect(cy.nodes().length).toBeGreaterThanOrEqual(0))
    }
    for (const body of [derivedSeedNetwork, closureDropNetwork, sharedEntryNetwork, methodFilterNetwork]) {
      withCy(build(graphOfNetwork(body)), (cy) => expect(cy.nodes().length).toBeGreaterThan(0))
    }
    expect([...vi.mocked(console.warn).mock.calls, ...vi.mocked(console.error).mock.calls]).toEqual([])
  })

  it('XD-8: a derived seed is never hidden', () => {
    const unsupported = (edge: GraphEdge): GraphEdge => ({
      ...edge,
      outcome_counts: { supported: 0, tested_absent: 0, inconclusive: 1, not_significant: 0 },
      independent_outcome_counts: { supported: 0, tested_absent: 0, inconclusive: 1, not_significant: 0 },
      assertion_count: 0, evidence_count: 0, distinct: { publications: 0, releases: 0, datasets: 0 }, by_basis: {},
    })
    const body: GraphNetworkResponse = {
      ...derivedSeedNetwork,
      query: { ...derivedSeedNetwork.query, outcome: ['all'] },
      edges: derivedSeedNetwork.edges.map((edge) => (edge.id === 'relationship:981' || edge.id === 'relationship:983' ? unsupported(edge) : edge)),
    }
    const graph = keptGraph(graphOfNetwork(body)).graph
    const hidden = visibleGraph(graph, { showNonSupporting: false })
    expect(hidden.nodes.map((node) => node.id)).toContain('entity:982')
    expect(hidden.hiddenNodes.map((node) => node.id)).toContain('entity:984')
    const shown = visibleGraph(graph, { showNonSupporting: true })
    expect(shown.nodes.map((node) => node.id)).toEqual(expect.arrayContaining(['entity:982', 'entity:984']))
  })

  it('XD-9: the legend lists the derived seed in its own entry with a dashed swatch', () => {
    const keys = (graph: TypedGraph) => legendEntries(graph, { vocabularies: vocab }).map((entry) => entry.key)
    expect(keys(tf1)).toEqual(['in-view', 'marker:tee', 'marker:vee-hollow', 'marker:symmetric', 'reference', 'level', 'family:physical',
      'family:regulation', 'node:gene', 'node:protein', 'node:protein_isoform', 'seed', 'derived', 'synthetic'])
    expect(legendEntries(tf1, { vocabularies: vocab }).find((entry) => entry.key === 'derived'))
      .toEqual({ key: 'derived', swatch: { kind: 'node', shape: 'protein', seed: false, derived: true }, text: DERIVED_LEGEND })
    expect(keys(net('network-tf1-expand-v1')).slice(-4)).toEqual(['seed', 'derived', 'synthetic', 'superseded'])
    expect(keys(net('network-tf1'))).toEqual(['in-view', 'marker:tee', 'marker:vee-hollow', 'family:regulation', 'node:gene', 'seed', 'synthetic'])
    render(<TypedLegend graph={tf1} vocabularies={vocab} />)
    const item = (text: string) => Array.from(document.querySelectorAll('li')).find((node) => node.textContent?.includes(text)) as HTMLElement
    const derived = Array.from(item(DERIVED_LEGEND).querySelectorAll('ellipse'))
    expect(derived).toHaveLength(3)
    expect(derived.map((node) => node.getAttribute('stroke-dasharray'))).toEqual(['4 2', '4 2', null])
    expect(LEGEND_TEXT.seed).toBe('Two-tone outline: a starting entity of this network (a seed).')
    const seed = Array.from(item(LEGEND_TEXT.seed).querySelectorAll('ellipse'))
    expect(seed).toHaveLength(3)
    expect(seed.map((node) => node.getAttribute('stroke-dasharray'))).toEqual([null, null, null])
  })

  it("XD-10: the notice words the closure's drop first, from status's cap", () => {
    const drop = graphOfNetwork(closureDropNetwork)
    const capped = 'At least 6 entities reached through gene-to-protein links were left out: a network looks up at most 26 entities, the chosen and the reached together.'
    const plain = 'At least 6 entities reached through gene-to-protein links were left out to keep the network within its limits.'
    expect(noticeLines(drop, { maxExpandedSeeds: 26 })).toEqual([derivedWords.derivedSeedsLeftText(6, 26)])
    expect(noticeLines(drop, { maxExpandedSeeds: 26 })).toEqual([capped])
    expect(noticeLines(drop)).toEqual([plain])
    expect(noticeLines(drop, { maxExpandedSeeds: null })).toEqual([plain])
    const both = graphOfNetwork({ ...closureDropNetwork, page: { ...closureDropNetwork.page, omitted: { derived_seeds: 6, edges: 3 } } })
    expect(noticeLines(both, { maxExpandedSeeds: 26 })).toEqual([capped, '4 relationships shown; 3 more that matched were left out to keep the network within its size limits.'])
    for (const lines of [noticeLines(drop, { maxExpandedSeeds: 26 }), noticeLines(drop), noticeLines(both, { maxExpandedSeeds: 26 })]) {
      for (const line of lines) expect(line).not.toContain('6 derived seeds omitted')
    }
    const { rerender } = render(<TypedNetworkNotice graph={drop} maxExpandedSeeds={26} />)
    const region = screen.getByRole('status')
    expect(region).toHaveAttribute('aria-live', 'polite')
    expect(Array.from(region.querySelectorAll('p'), (node) => node.textContent)).toEqual([capped])
    rerender(<TypedNetworkNotice graph={drop} />)
    expect(screen.getByRole('status')).toBe(region)
    expect(Array.from(region.querySelectorAll('p'), (node) => node.textContent)).toEqual([plain])
  })

  it('XD-11: the table names derived seeds by their paths', () => {
    const options = { showNonSupporting: false, vocabularies: vocab }
    expect(entityRows(tf1, options).map((row) => row.name)).toEqual([
      'SYN-G1 (Gene)', 'SYN-G4 (Gene)', 'SYN-TF1 (Gene), seed', 'SYN-P1 (Protein (UniProtKB entry)), reached via Encodes',
      'SYN-P1-1 (Protein isoform), reached via Encodes, then Isoform of', 'SYN-P2 (Protein (UniProtKB entry))',
    ])
    expect(entityRows(graphOfNetwork(sharedEntryNetwork), options).map((row) => row.name)).toEqual([
      'SYN-N985 (Protein (UniProtKB entry)), seed', 'SYN-N986 (Gene), reached via Encodes', 'SYN-N987 (Gene), reached via Encodes',
      'SYN-N988 (Gene), reached via Encodes', 'SYN-N985-1 (Protein isoform), reached via Isoform of',
    ])
    expect(entitySuffix(nodeOf(tf1, 'entity:7'), tf1, vocab)).toBe(', seed')
    expect(entitySuffix(nodeOf(tf1, 'entity:12'), tf1, vocab)).toBe('')
    expect(entitySuffix(nodeOf(tf1, 'entity:10'), tf1, vocab)).toBe(', reached via Encodes')
    expect(edgeRows(tf1, options).map((row) => row.name)).toEqual([
      'Physical interaction between SYN-P1 (Protein (UniProtKB entry)) and SYN-P2 (Protein (UniProtKB entry))',
      'Transcriptional regulation: SYN-G1 (Gene) to SYN-TF1 (Gene)',
      'Transcriptional regulation: SYN-TF1 (Gene) to SYN-G1 (Gene)',
      'Transcriptional regulation: SYN-TF1 (Gene) to SYN-G4 (Gene)',
      ENCODES_ROW,
      'Isoform of: SYN-P1-1 (Protein isoform) to SYN-P1 (Protein (UniProtKB entry))',
    ])
  })

  it('XD-12: the entity panel states the path before its body loads', async () => {
    const path = derivationText('SYN-TF1 (Gene)', 'via Encodes, then Isoform of', ['Synthetic regulation example v2'])
    render(<TypedSelectionPanel selection={{ kind: 'node', id: 'entity:11', record: nodeOf(tf1, 'entity:11') }} graph={tf1} vocabularies={vocab} onClose={() => {}} />,
      { wrapper: graphWrapper(newGraphClient()) })
    const region = screen.getByRole('region', { name: 'SYN-P1-1 (Protein isoform)' })
    const loading = await within(region).findByText(LOADING_ENTITY, {}, GRAPH_WAIT)
    const sentence = within(region).getByText(path)
    const heading = within(region).getByRole('heading')
    expect(heading.compareDocumentPosition(sentence) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(sentence.compareDocumentPosition(loading) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    await waitFor(() => expect(asked()).toEqual([ENTITY_11]), GRAPH_WAIT)
    await within(region).findByText(PROVISIONAL, {}, GRAPH_WAIT)
    expect(within(region).getAllByText(path)).toHaveLength(1)
    cleanup()
    render(<TypedSelectionPanel selection={{ kind: 'node', id: 'entity:7', record: nodeOf(tf1, 'entity:7') }} graph={tf1} vocabularies={vocab} onClose={() => {}} />,
      { wrapper: graphWrapper(newGraphClient()) })
    const seed = screen.getByRole('region', { name: 'SYN-TF1 (Gene)' })
    await within(seed).findByText(PROVISIONAL, {}, GRAPH_WAIT)
    expect(Array.from(seed.querySelectorAll('p')).filter((node) => (node.textContent ?? '').startsWith('Reached from'))).toEqual([])
    expect(asked()).toEqual([ENTITY_11, ENTITY_7])
    cleanup()
    render(<EntityPanel entityRef="entity:11" selection={{}} />, { wrapper: graphWrapper(newGraphClient()) })
    await screen.findByText(PROVISIONAL, {}, GRAPH_WAIT)
    expect(Array.from(document.querySelectorAll('p')).filter((node) => (node.textContent ?? '').startsWith('Reached from'))).toEqual([])
    expect(asked()[asked().length - 1]).toBe(ENTITY_11)
  }, 20_000)

  it("XD-13: a join's panel sends no filter while a ranked edge keeps its own", async () => {
    const recordLine = (name: string) => {
      const row = recorded<GraphEvidenceResponse>(name).body.evidence[0]
      return `${row.record.key}, line ${row.record.line_number} (${row.id})`
    }
    const filtered = graphOfNetwork(echoed({ evidence_basis: ['experimental'] }))
    render(<TypedSelectionPanel selection={{ kind: 'edge', id: 'relationship:1' }} graph={filtered} vocabularies={vocab} onClose={() => {}} />,
      { wrapper: graphWrapper(newGraphClient()) })
    const region = screen.getByRole('region', { name: ENCODES_ROW })
    await waitFor(() => expect(asked()).toEqual([RELATIONSHIP_1, EVIDENCE_1]), GRAPH_WAIT)
    await within(region).findByText(recordLine('evidence-encodes'), {}, GRAPH_WAIT)
    expect(region.textContent ?? '').not.toContain(NOT_LISTED)
    cleanup()
    const physical = '/api/graph/v1/relationships/relationship%3A3?evidence_basis=experimental'
    const physicalEvidence = '/api/graph/v1/relationships/relationship%3A3/evidence?evidence_basis=experimental'
    const encodesReleases = '/api/graph/v1/relationships/relationship%3A1?releases=release:1'
    const encodesReleasesEvidence = '/api/graph/v1/relationships/relationship%3A1/evidence?releases=release:1'
    const stubs: Record<string, string> = {
      [physical]: 'relationship-physical', [physicalEvidence]: 'evidence-physical',
      [encodesReleases]: 'relationship-encodes', [encodesReleasesEvidence]: 'evidence-encodes',
    }
    const answer = ({ request }: { request: Request }) => {
      const url = new URL(request.url)
      const name = stubs[url.pathname + url.search]
      return name === undefined ? undefined : HttpResponse.json(recorded<object>(name).body)
    }
    server.use(http.get('/api/graph/v1/relationships/:ref', answer), http.get('/api/graph/v1/relationships/:ref/evidence', answer))
    let before = asked().length
    render(<TypedSelectionPanel selection={{ kind: 'edge', id: 'relationship:3' }} graph={filtered} vocabularies={vocab} onClose={() => {}} />,
      { wrapper: graphWrapper(newGraphClient()) })
    await waitFor(() => expect(asked().slice(before)).toEqual([physical, physicalEvidence]), GRAPH_WAIT)
    await waitFor(() => expect(answers.answers).toEqual(expect.arrayContaining([`200 ${physical}`, `200 ${physicalEvidence}`])), GRAPH_WAIT)
    await within(screen.getByRole('region')).findByText(recordLine('evidence-physical'), {}, GRAPH_WAIT)
    cleanup()
    before = asked().length
    render(<TypedSelectionPanel selection={{ kind: 'edge', id: 'relationship:1' }} graph={graphOfNetwork(echoed({ releases: ['release:1'] }))} vocabularies={vocab} onClose={() => {}} />,
      { wrapper: graphWrapper(newGraphClient()) })
    await waitFor(() => expect(asked().slice(before)).toEqual([encodesReleases, encodesReleasesEvidence]), GRAPH_WAIT)
    await within(screen.getByRole('region')).findByText(recordLine('evidence-encodes'), {}, GRAPH_WAIT)
    expect(asked().filter((url) => url.includes('expand'))).toEqual([])
  }, 20_000)
})
