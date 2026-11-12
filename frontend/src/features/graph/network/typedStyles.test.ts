import cytoscape from 'cytoscape'
import type { Core, ElementDefinition, StylesheetJsonBlock } from 'cytoscape'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  directionOnlyEdge, inconclusiveEdge, levelAssignedEdge, makeGraphEdge, makeNetworkNode, makeNetworkResponse,
  mixedSignEdge, negativeSignEdge, notSignificantEdge, positiveSignEdge, ppiEdge, ppiNodes, publicationEdges,
  roleOrderedEdge, unknownChannelsEdge, unknownLevelEdge, unresolvedReferenceEdge, unspecifiedEdge,
} from '../../../mocks/fixtures/typedGraph/extras'
import { recorded } from '../../../test/graphHooks'
import type { GraphEdge, GraphEntityResponse, GraphFamily, GraphFiltersEcho, GraphNetworkResponse, GraphOutcome } from '../types'
import { edgeWidthPx } from './channels'
import { EMPTY_GRAPH, graphOfNetwork } from './graph'
import type { TypedGraph } from './graph'
import {
  COLOUR_NAMES, FAMILY_COLOURS, LABEL_OUTLINE, LABEL_TEXT, NEUTRAL, NODE_FILL, PAGE_BACKGROUNDS, SELECTION,
  TESTED_ABSENT_OPACITY,
} from './palette'
import { buildElements } from './typedElements'
import type { BuildOptions } from './typedElements'
import { TYPED_STYLESHEET } from './typedStyles'

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

// WCAG 2.x relative luminance and contrast; no pixel is drawn in jsdom, so contrast is computed from the colours.
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

/** Source-over in sRGB, rounded to the 8 bits a screen draws. */
function blend(colour: string, background: string, opacity: number): string {
  const top = rgbParts(colour)
  const bottom = rgbParts(background)
  const mixed = top.map((value, index) => Math.round(value * opacity + bottom[index] * (1 - opacity)))
  return `#${mixed.map((value) => value.toString(16).padStart(2, '0')).join('')}`
}

/** Cytoscape reports a colour in this form, never as the palette's hex. */
function rgb(hex: string): string {
  return `rgb(${rgbParts(hex).join(',')})`
}

const BACKGROUNDS = [PAGE_BACKGROUNDS.light, PAGE_BACKGROUNDS.dark]
const NO_OUTCOMES = { supported: 0, tested_absent: 0, inconclusive: 0, not_significant: 0 }

/** One instance per case, destroyed after it: a headless core with styles runs an animation loop until then. */
function withCy(elements: ElementDefinition[], check: (cy: Core) => void): void {
  const cy = cytoscape({ headless: true, styleEnabled: true, elements, style: TYPED_STYLESHEET })
  try {
    check(cy)
  } finally {
    cy.destroy()
  }
}

function drawn(graph: TypedGraph, options: BuildOptions): ElementDefinition[] {
  return buildElements(graph, options).elements
}

function network(name: string): TypedGraph {
  return graphOfNetwork(recorded<GraphNetworkResponse>(name).body)
}

function entityGraph(name: string): TypedGraph {
  const body = recorded<GraphEntityResponse>(name).body
  return { ...EMPTY_GRAPH, nodes: body.nodes, edges: body.reference_relationships, releases: body.releases }
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

function withoutSupport(id: string, counts: Partial<Record<GraphOutcome, number>>): GraphEdge {
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
  })
}

function styleOf(block: StylesheetJsonBlock): Record<string, unknown> {
  return Object.fromEntries(Object.entries('style' in block ? block.style : block.css))
}

/** A selector named twice would let the later block override the one a row reads. */
function blockStyle(selector: string): Record<string, unknown> {
  const blocks = TYPED_STYLESHEET.filter((block) => block.selector === selector)
  expect(blocks, selector).toHaveLength(1)
  return styleOf(blocks[0])
}

const noCountsEdge = withoutSupport('relationship:933', {})
const testedAbsentEdge = withoutSupport('relationship:934', { tested_absent: 1 })

describe('contrast', () => {
  it('the helpers', () => {
    expect(contrastRatio('#000000', '#ffffff')).toBe(21)
    expect(blend('#000000', '#ffffff', 0.5)).toBe('#808080')
    expect(blend('#6B7280', '#fafafa', 1)).toBe('#6b7280')
    expect(rgb('#0072B2')).toBe('rgb(0,114,178)')
  })

  it.each<[string, string, number, number]>([
    ['Okabe-Ito blue', '#0072B2', 4.97, 3.72],
    ['Okabe-Ito bluish green', '#009E73', 3.28, 5.64],
    ['grey', '#6B7280', 4.63, 3.99],
    ['the node fill', '#7C8798', 3.48, 5.31],
    ["Paul Tol's purple", '#AA3377', 5.83, 3.17],
  ])("the helpers reproduce the palette's measured ratios for %s", (_name, colour, light, dark) => {
    expect(contrastRatio(colour, '#fafafa')).toBeCloseTo(light, 2)
    expect(contrastRatio(colour, '#0a0e17')).toBeCloseTo(dark, 2)
  })

  it.each<[string, (background: string) => string]>([
    ...Object.entries(FAMILY_COLOURS).map(([family, colour]): [string, (background: string) => string] => [
      `the ${family} family's colour`,
      () => colour,
    ]),
    ['grey', () => NEUTRAL],
    ['grey at the tested-absent opacity', (background) => blend(NEUTRAL, background, TESTED_ABSENT_OPACITY)],
    ['the node fill', () => NODE_FILL],
    ['the selection', () => SELECTION],
  ])('%s has 3:1 on both page backgrounds', (_name, drawnOn) => {
    for (const background of BACKGROUNDS) {
      expect(contrastRatio(drawnOn(background), background), background).toBeGreaterThanOrEqual(3)
    }
  })

  it("the seed ring's two colours each have 3:1 against the node fill, and one of them 3:1 against each page", () => {
    expect(contrastRatio(LABEL_OUTLINE, NODE_FILL)).toBeGreaterThanOrEqual(3)
    expect(contrastRatio(LABEL_TEXT, NODE_FILL)).toBeGreaterThanOrEqual(3)
    for (const background of BACKGROUNDS) {
      const best = Math.max(contrastRatio(LABEL_OUTLINE, background), contrastRatio(LABEL_TEXT, background))
      expect(best, background).toBeGreaterThanOrEqual(3)
    }
  })

  it('no family takes the colour of the selection, grey or the node fill, and every family colour has a name', () => {
    expect(Object.keys(FAMILY_COLOURS)).toEqual(expect.arrayContaining(['regulation', 'physical']))
    const taken = [SELECTION, NEUTRAL, NODE_FILL].map((colour) => colour.toUpperCase())
    for (const [family, colour] of Object.entries(FAMILY_COLOURS)) {
      expect(taken, family).not.toContain(colour.toUpperCase())
      expect(Object.prototype.hasOwnProperty.call(COLOUR_NAMES, colour), family).toBe(true)
    }
  })
})

describe('markers', () => {
  it('a filled triangle, a tee, a filled diamond and a hollow vee for the four effect summaries', () => {
    withCy(drawn(network('network-all-edges'), { showNonSupporting: true }), (cy) => {
      const marker = (id: string) => {
        const edge = cy.getElementById(id)
        return [edge.style('target-arrow-shape'), edge.style('target-arrow-fill')]
      }
      for (const id of ['relationship:10', 'relationship:11', 'relationship:14']) {
        expect(cy.getElementById(id).hasClass('mk-triangle'), id).toBe(true)
        expect(marker(id), id).toEqual(['triangle', 'filled'])
      }
      for (const id of ['relationship:5', 'relationship:6']) {
        expect(cy.getElementById(id).hasClass('mk-tee'), id).toBe(true)
        expect(marker(id)[0], id).toBe('tee')
      }
      expect(cy.getElementById('relationship:9').hasClass('mk-diamond')).toBe(true)
      expect(marker('relationship:9')).toEqual(['diamond', 'filled'])
      for (const id of ['relationship:8', 'relationship:12', 'relationship:13']) {
        expect(cy.getElementById(id).hasClass('mk-vee-hollow'), id).toBe(true)
        expect(marker(id), id).toEqual(['vee', 'hollow'])
      }
    })
  })

  it.each([
    ['network-tf1-v1', 'mk-triangle', 'triangle'],
    ['network-tf1', 'mk-tee', 'tee'],
    ['network-tf1-both-releases', 'mk-diamond', 'diamond'],
  ])('relationship:6 in %s takes %s', (name, marker, shape) => {
    withCy(drawn(network(name), { showNonSupporting: false }), (cy) => {
      const edge = cy.getElementById('relationship:6')
      expect(edge.hasClass(marker)).toBe(true)
      expect(edge.style('target-arrow-shape')).toBe(shape)
    })
  })

  it('a filled circle for the role-ordered type, a chevron on a reference link and a filled square for a direction only', () => {
    const unknown = makeGraphEdge({
      id: 'relationship:931',
      source: 'entity:931',
      target: 'entity:932',
      effect: 'unknown',
      effect_counts: { activating: 0, inhibiting: 0, unknown: 1, not_stated: 0 },
    })
    const graph = handGraph([roleOrderedEdge, directionOnlyEdge, unresolvedReferenceEdge, unknown])
    withCy(drawn(graph, { showNonSupporting: true }), (cy) => {
      const circle = cy.getElementById(roleOrderedEdge.id)
      expect(circle.hasClass('mk-circle')).toBe(true)
      expect([circle.style('target-arrow-shape'), circle.style('target-arrow-fill')]).toEqual(['circle', 'filled'])
      const square = cy.getElementById(directionOnlyEdge.id)
      expect(square.hasClass('mk-square-direction')).toBe(true)
      expect([square.style('target-arrow-shape'), square.style('target-arrow-fill')]).toEqual(['square', 'filled'])
      expect(square.style('target-arrow-color')).toBe(rgb(FAMILY_COLOURS.regulation as string))
      const chevron = cy.getElementById(unresolvedReferenceEdge.id)
      expect([chevron.hasClass('mk-chevron-reference'), chevron.hasClass('ln-reference')]).toEqual([true, true])
      expect([chevron.style('target-arrow-shape'), chevron.style('target-arrow-fill')]).toEqual(['chevron', 'filled'])
      expect(cy.getElementById(unknown.id).hasClass('mk-vee-hollow')).toBe(true)
      expect(cy.getElementById(unknown.id).style('target-arrow-fill')).toBe('hollow')
    })
  })

  it("draws entity-tf1's encodes link thin and grey, with a chevron", () => {
    withCy(drawn(entityGraph('entity-tf1'), { showNonSupporting: true }), (cy) => {
      const link = cy.getElementById('relationship:1')
      expect([link.hasClass('mk-chevron-reference'), link.hasClass('ln-reference')]).toEqual([true, true])
      expect(link.style('target-arrow-shape')).toBe('chevron')
      expect([link.style('line-color'), link.style('target-arrow-color'), link.style('width')]).toEqual([rgb(NEUTRAL), rgb(NEUTRAL), '1px'])
    })
  })

  it('no end marker on a symmetric edge, beside a tee', () => {
    withCy(drawn(network('network-all-edges'), { showNonSupporting: true }), (cy) => {
      for (const id of ['relationship:3', 'relationship:4']) {
        expect(cy.getElementById(id).hasClass('mk-none'), id).toBe(true)
        expect(cy.getElementById(id).style('target-arrow-shape'), id).toBe('none')
      }
      expect(cy.getElementById('relationship:5').hasClass('mk-tee')).toBe(true)
      expect(cy.getElementById('relationship:5').style('target-arrow-shape')).toBe('tee')
    })
  })

  it('every end marker at the target: the one block that sets a source arrow sets none', () => {
    const sourceArrows = TYPED_STYLESHEET
      .map(styleOf)
      .filter((style) => 'source-arrow-shape' in style)
      .map((style) => style['source-arrow-shape'])
    expect(sourceArrows).toEqual(['none'])
  })
})

describe('lines', () => {
  it('relationship:7 is dashed, and hidden only while edges without support are hidden', () => {
    const graph = network('network-all-edges')
    withCy(drawn(graph, { showNonSupporting: false }), (cy) => {
      const edge = cy.getElementById('relationship:7')
      expect([edge.hasClass('ln-tested-absent'), edge.hasClass('hidden')]).toEqual([true, true])
      expect(edge.style('line-style')).toBe('dashed')
      expect(edge.style('display')).toBe('none')
      expect(edge.visible()).toBe(false)
    })
    withCy(drawn(graph, { showNonSupporting: true }), (cy) => {
      const edge = cy.getElementById('relationship:7')
      expect([edge.hasClass('ln-tested-absent'), edge.hasClass('hidden')]).toEqual([true, false])
      expect(edge.style('display')).toBe('element')
      expect(edge.visible()).toBe(true)
      expect(edge.style('line-style')).toBe('dashed')
      expect([edge.style('opacity'), edge.style('width')]).toEqual(['0.85', '1.5px'])
      const solid = cy.getElementById('relationship:4')
      expect(solid.hasClass('ln-supported')).toBe(true)
      expect([solid.style('line-style'), solid.style('opacity')]).toEqual(['solid', '1'])
    })
  })

  it("states the tested-absent dash pattern, which is also Cytoscape's default, in its block", () => {
    expect(blockStyle('edge.ln-tested-absent')['line-dash-pattern']).toEqual([6, 3])
  })

  it('relationship:13, predicted only, is dotted beside a solid line', () => {
    withCy(drawn(network('network-all-edges'), { showNonSupporting: true }), (cy) => {
      const predicted = cy.getElementById('relationship:13')
      expect(predicted.hasClass('ln-predicted')).toBe(true)
      expect(predicted.style('line-style')).toBe('dotted')
      const solid = cy.getElementById('relationship:14')
      expect([solid.hasClass('ln-supported'), solid.style('line-style')]).toEqual([true, 'solid'])
    })
  })

  it('every edge is a bezier curve, the self-loop is drawn, and relationship:5 has its reverse beside it', () => {
    withCy(drawn(network('network-all-edges'), { showNonSupporting: true }), (cy) => {
      expect(cy.edges()).toHaveLength(12)
      const lineClasses = ['ln-supported', 'ln-predicted', 'ln-tested-absent', 'ln-not-significant', 'ln-inconclusive', 'ln-non-support', 'ln-reference']
      cy.edges().forEach((edge) => {
        expect(lineClasses.filter((name) => edge.hasClass(name)), edge.id()).toHaveLength(1)
        expect(edge.style('curve-style'), edge.id()).toBe('bezier')
      })
      const loop = cy.getElementById('relationship:10')
      expect([loop.length, loop.isLoop()]).toEqual([1, true])
      expect(cy.getElementById('relationship:5').parallelEdges().map((edge) => edge.id()).sort()).toEqual([
        'relationship:5',
        'relationship:6',
      ])
    })
  })

  it('the not-significant, inconclusive and non-support lines are thin and grey, with a hollow square at the middle', () => {
    const supported = makeGraphEdge({ id: 'relationship:932', source: 'entity:931', target: 'entity:932' })
    const graph = handGraph([notSignificantEdge, inconclusiveEdge, noCountsEdge, supported], ['all'])
    withCy(drawn(graph, { showNonSupporting: true }), (cy) => {
      for (const [edge, line] of [
        [notSignificantEdge, 'ln-not-significant'],
        [inconclusiveEdge, 'ln-inconclusive'],
        [noCountsEdge, 'ln-non-support'],
      ] as const) {
        const element = cy.getElementById(edge.id)
        expect(element.hasClass(line), edge.id).toBe(true)
        expect([element.style('mid-target-arrow-shape'), element.style('mid-target-arrow-fill')], edge.id).toEqual(['square', 'hollow'])
        expect([element.style('line-color'), element.style('target-arrow-color'), element.style('width')], edge.id).toEqual([
          rgb(NEUTRAL),
          rgb(NEUTRAL),
          '1px',
        ])
      }
      const control = cy.getElementById(supported.id)
      expect(control.hasClass('ln-supported')).toBe(true)
      expect([control.style('mid-target-arrow-shape'), control.style('mid-target-arrow-fill')]).toEqual(['none', 'filled'])
    })
  })
})

describe('colours', () => {
  it("draws each recorded family's supported lines in its colour, and a tested-absent line grey", () => {
    withCy(drawn(network('network-all-edges'), { showNonSupporting: true }), (cy) => {
      const colours = (id: string) => [cy.getElementById(id).style('line-color'), cy.getElementById(id).style('target-arrow-color')]
      const regulation = rgb(FAMILY_COLOURS.regulation ?? 'none')
      const physical = rgb(FAMILY_COLOURS.physical ?? 'none')
      for (const id of ['relationship:9', 'relationship:3']) expect(cy.getElementById(id).hasClass('ln-supported'), id).toBe(true)
      expect(cy.getElementById('relationship:7').hasClass('ln-tested-absent')).toBe(true)
      expect(cy.getElementById('relationship:9').hasClass('fam-regulation')).toBe(true)
      expect(colours('relationship:9')).toEqual([regulation, regulation])
      expect(cy.getElementById('relationship:3').hasClass('fam-physical')).toBe(true)
      expect(colours('relationship:3')).toEqual([physical, physical])
      expect(cy.getElementById('relationship:7').hasClass('fam-regulation')).toBe(true)
      expect(colours('relationship:7')).toEqual([rgb(NEUTRAL), rgb(NEUTRAL)])
    })
  })

  it('draws every family of the palette in its colour, and every other family grey', () => {
    const coloured = Object.entries(FAMILY_COLOURS).map(([family, colour], index) => ({
      edge: makeGraphEdge({ id: `relationship:96${index}`, family: family as GraphFamily, source: 'entity:960', target: `entity:96${index + 1}` }),
      colour,
    }))
    const graph = handGraph([...coloured.map(({ edge }) => edge), mixedSignEdge, roleOrderedEdge])
    withCy(drawn(graph, { showNonSupporting: true }), (cy) => {
      const colours = (id: string) => [cy.getElementById(id).style('line-color'), cy.getElementById(id).style('target-arrow-color')]
      for (const { edge, colour } of coloured) {
        expect([cy.getElementById(edge.id).hasClass(`fam-${edge.family}`), cy.getElementById(edge.id).hasClass('ln-supported')], edge.family)
          .toEqual([true, true])
        expect(colours(edge.id), edge.family).toEqual([rgb(colour), rgb(colour)])
      }
      for (const edge of [mixedSignEdge, roleOrderedEdge]) expect(colours(edge.id), edge.family).toEqual([rgb(NEUTRAL), rgb(NEUTRAL)])
    })
  })

  it('rings a seed in two tones', () => {
    withCy(drawn(network('network-tf1'), { showNonSupporting: false }), (cy) => {
      const seed = cy.getElementById('entity:7')
      expect(seed.hasClass('seed')).toBe(true)
      expect([seed.style('border-color'), seed.style('border-width')]).toEqual([rgb(LABEL_OUTLINE), '3px'])
      expect([seed.style('outline-color'), seed.style('outline-width')]).toEqual([rgb(LABEL_TEXT), '2px'])
      const other = cy.getElementById('entity:1')
      expect([other.hasClass('seed'), other.style('border-width'), other.style('outline-width')]).toEqual([false, '0px', '0px'])
      expect([seed.style('background-color'), other.style('background-color')]).toEqual([rgb(NODE_FILL), rgb(NODE_FILL)])
    })
  })

  it('draws the selection as an opaque underlay, on an edge or a node', () => {
    withCy(drawn(network('network-all-edges'), { showNonSupporting: true, selectedId: 'relationship:9' }), (cy) => {
      const selected = cy.getElementById('relationship:9')
      expect(selected.hasClass('is-selected')).toBe(true)
      expect([selected.style('underlay-color'), selected.style('underlay-opacity'), selected.style('underlay-padding')]).toEqual([
        rgb(SELECTION),
        '1',
        '6px',
      ])
      expect(cy.getElementById('relationship:14').style('underlay-opacity')).toBe('0')
    })
    withCy(drawn(network('network-tf1'), { showNonSupporting: false, selectedId: 'entity:7' }), (cy) => {
      const selected = cy.getElementById('entity:7')
      expect(selected.hasClass('is-selected')).toBe(true)
      expect([selected.style('underlay-color'), selected.style('underlay-opacity')]).toEqual([rgb(SELECTION), '1'])
      expect(cy.getElementById('entity:1').style('underlay-opacity')).toBe('0')
    })
  })

  it('draws a selected edge in the label colour, with 3:1 against the underlay beneath it', () => {
    for (const id of ['relationship:13', 'relationship:7']) {
      withCy(drawn(network('network-all-edges'), { showNonSupporting: true, selectedId: id }), (cy) => {
        const selected = cy.getElementById(id)
        expect([selected.hasClass('is-selected'), selected.hasClass(id === 'relationship:7' ? 'ln-tested-absent' : 'ln-predicted')], id)
          .toEqual([true, true])
        expect([selected.style('line-color'), selected.style('target-arrow-color'), selected.style('mid-target-arrow-color')], id)
          .toEqual([rgb(LABEL_TEXT), rgb(LABEL_TEXT), rgb(LABEL_TEXT)])
        // The control: an edge that is not selected keeps its family's colour.
        expect(cy.getElementById('relationship:14').style('line-color'), id).toBe(rgb(FAMILY_COLOURS.regulation as string))
      })
    }
    expect(contrastRatio(LABEL_TEXT, SELECTION)).toBeGreaterThanOrEqual(3)
    expect(contrastRatio(blend(LABEL_TEXT, SELECTION, TESTED_ABSENT_OPACITY), SELECTION)).toBeGreaterThanOrEqual(3)
  })

  it('shapes each entity type', () => {
    const graph = network('network-p1-reference')
    const other = makeNetworkNode({ id: 'entity:970', type: 'syn_chemical', label: 'SYN-CHEMICAL' })
    withCy(drawn({ ...graph, nodes: [...graph.nodes, other] }, { showNonSupporting: true }), (cy) => {
      const shape = (id: string, name: string) => {
        expect(cy.getElementById(id).hasClass(name), id).toBe(true)
        return cy.getElementById(id).style('shape')
      }
      expect(shape('entity:7', 'sh-gene')).toBe('bottom-round-rectangle')
      expect(shape('entity:10', 'sh-protein')).toBe('ellipse')
      expect(shape('entity:11', 'sh-isoform')).toBe('round-diamond')
      expect(shape('entity:970', 'sh-other')).toBe('round-triangle')
    })
  })
})

describe('width', () => {
  it("draws each line's width as edgeWidthPx gives it", () => {
    const [none, two] = [0, 2].map((publications) =>
      makeGraphEdge({ id: `relationship:94${publications}`, distinct: { publications, releases: 1, datasets: 1 } }),
    )
    const supported = [none, publicationEdges[0], two, ...publicationEdges.slice(1)]
    expect(supported.map((edge) => edge.distinct.publications)).toEqual([0, 1, 2, 3, 5, 9])
    const predicted = makeGraphEdge({
      id: 'relationship:948',
      by_basis: { computational_prediction: 1 },
      distinct: { publications: 3, releases: 1, datasets: 1 },
    })
    const graph = handGraph(
      [...supported, predicted, testedAbsentEdge, notSignificantEdge, inconclusiveEdge, noCountsEdge, unresolvedReferenceEdge],
      ['all'],
    )
    withCy(drawn(graph, { showNonSupporting: true }), (cy) => {
      const width = (edge: GraphEdge): string => cy.getElementById(edge.id).style('width')
      const lines: [GraphEdge, string][] = [
        ...supported.map((edge): [GraphEdge, string] => [edge, 'ln-supported']),
        [predicted, 'ln-predicted'],
        [testedAbsentEdge, 'ln-tested-absent'],
        [notSignificantEdge, 'ln-not-significant'],
        [inconclusiveEdge, 'ln-inconclusive'],
        [noCountsEdge, 'ln-non-support'],
        [unresolvedReferenceEdge, 'ln-reference'],
      ]
      for (const [edge, line] of lines) expect(cy.getElementById(edge.id).hasClass(line), edge.id).toBe(true)
      expect(supported.map(width)).toEqual(['1.5px', '1.5px', '2.5px', '3.5px', '5.5px', '5.5px'])
      expect([width(predicted), width(testedAbsentEdge), width(notSignificantEdge)]).toEqual(['3.5px', '1.5px', '1px'])
      for (const edge of graph.edges) {
        expect(parseFloat(width(edge)), edge.id).toBe(edgeWidthPx(edge, graph.requestedOutcomes))
      }
    })
  })
})

describe('warnings', () => {
  it('Cytoscape warns of nothing on any recorded network or hand-written shape', () => {
    const names = [
      'network-tf1', 'network-tf1-outcome-all', 'network-tf1-v1', 'network-tf1-both-releases', 'network-all-edges',
      'network-tf1-browser', 'network-tf1-visitor', 'network-p1-reference', 'network-tf1-tested-absent',
      'network-p2-tested-absent', 'network-truncated', 'network-p2-inconclusive',
    ]
    for (const name of names) {
      withCy(drawn(network(name), { showNonSupporting: false, selectedId: 'entity:7' }), (cy) => {
        expect(cy.nodes()).toHaveLength(recorded<GraphNetworkResponse>(name).body.nodes.length)
      })
      withCy(drawn(network(name), { showNonSupporting: true, selectedId: 'relationship:6' }), () => undefined)
    }
    withCy(drawn(entityGraph('entity-tf1'), { showNonSupporting: true }), () => undefined)
    const shapes = [
      roleOrderedEdge, directionOnlyEdge, notSignificantEdge, inconclusiveEdge, negativeSignEdge, positiveSignEdge,
      mixedSignEdge, levelAssignedEdge, unspecifiedEdge, unknownChannelsEdge, unknownLevelEdge, ...publicationEdges,
      unresolvedReferenceEdge, noCountsEdge, testedAbsentEdge,
    ]
    withCy(drawn(handGraph(shapes, ['all']), { showNonSupporting: false }), () => undefined)
    withCy(drawn(handGraph(shapes, ['all']), { showNonSupporting: true }), () => undefined)
    withCy(drawn(graphOfNetwork(makeNetworkResponse({ nodes: ppiNodes, edges: [ppiEdge] })), { showNonSupporting: true }), () => undefined)
    expect(vi.mocked(console.warn)).not.toHaveBeenCalled()
    expect(vi.mocked(console.error)).not.toHaveBeenCalled()
  })
})
