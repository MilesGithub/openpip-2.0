import { cleanup, render } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  directionOnlyEdge, inconclusiveEdge, levelAssignedEdge, ligandReceptorType, makeGraphEdge, makeNetworkNode,
  makeNetworkResponse, mixedReferenceEdge, mixedSignEdge, negativeSignEdge, notSignificantEdge, positiveSignEdge,
  roleOrderedEdge, testedAbsentReferenceEdge, testedAbsentRelease, unknownChannelsEdge, unknownLevelEdge,
  unresolvedReferenceEdge, unspecifiedEdge,
} from '../../../mocks/fixtures/typedGraph/extras'
import { recorded } from '../../../test/graphHooks'
import type { GraphEdge, GraphEntityResponse, GraphNetworkResponse, GraphVocabularies } from '../types'
import { EMPTY_GRAPH, graphOfNetwork, keptGraph } from '../network/graph'
import type { TypedGraph } from '../network/graph'
import { noticeLines } from '../network/notice'
import { NEUTRAL, TESTED_ABSENT_OPACITY } from '../network/palette'
import { TypedNetworkNotice } from '../network/TypedNetworkNotice'
import { defaultShowNonSupporting, visibleGraph } from '../network/visibility'
import { LEVEL_ASSIGNED_WORDS, LEVEL_WORDS, SIGN_WORDS, edgeDescription, edgeLabel, typeLabel } from '../network/words'
import { legendEntries, lineSwatchWidthPx } from './legendEntries'
import type { LegendEntry } from './legendEntries'
import { LEGEND_HEADING, LEGEND_TEXT, SHAPE_WORDS } from './legendText'
import { TypedLegend } from './TypedLegend'

// Cytoscape and React report a bad value or an unwrapped update through the console instead of throwing.
const REPORTED = /cytoscape|is invalid|not a valid|act\(/i

beforeEach(() => {
  vi.spyOn(console, 'warn')
  vi.spyOn(console, 'error')
})

afterEach(() => {
  // Unmounted here rather than after this hook, so that what the unmount reports is checked too.
  cleanup()
  const calls = [...vi.mocked(console.warn).mock.calls, ...vi.mocked(console.error).mock.calls]
  vi.restoreAllMocks()
  expect(calls.map((args) => args.map(String).join(' ')).filter((text) => REPORTED.test(text))).toEqual([])
})

const RECORDED_NETWORKS = [
  'network-tf1', 'network-tf1-outcome-all', 'network-tf1-v1', 'network-tf1-both-releases', 'network-all-edges',
  'network-tf1-browser', 'network-p1-reference', 'network-tf1-tested-absent', 'network-p2-tested-absent',
  'network-truncated', 'network-p2-inconclusive',
]

function network(name: string): TypedGraph {
  return graphOfNetwork(recorded<GraphNetworkResponse>(name).body)
}

function vocabularies(): GraphVocabularies {
  return recorded<GraphVocabularies>('vocabularies').body
}

/** The edges on makeNetworkResponse's two nodes, the seed entity:901 and entity:902, answering the default request. */
function handWritten(...edges: GraphEdge[]): TypedGraph {
  return graphOfNetwork(makeNetworkResponse({ edges }))
}

function keys(graph: TypedGraph | null, options?: Parameters<typeof legendEntries>[1]): string[] {
  return legendEntries(graph, options).map((entry) => entry.key)
}

function entry(entries: LegendEntry[], key: string): LegendEntry {
  const found = entries.find((candidate) => candidate.key === key)
  if (found === undefined) throw new Error(`no legend entry ${key} in ${entries.map((item) => item.key).join(', ')}`)
  return found
}

/** Each entry's swatch as TypedLegend renders it, by the entry's key. */
function swatchesOf(graph: TypedGraph, showNonSupporting?: boolean): Map<string, SVGSVGElement> {
  const { container } = render(<TypedLegend graph={graph} showNonSupporting={showNonSupporting} />)
  const items = Array.from(container.querySelectorAll('li'))
  const entries = legendEntries(graph, { showNonSupporting })
  expect(items).toHaveLength(entries.length)
  return new Map(entries.map((item, index) => [item.key, items[index].querySelector('svg') as SVGSVGElement]))
}

/** No supporting result and no outcome counted: the line drawn for neither support nor a stated outcome. */
const bareNonSupportEdge = makeGraphEdge({
  id: 'relationship:914',
  effect: null,
  effect_counts: { activating: 0, inhibiting: 0, unknown: 0, not_stated: 0 },
  sign_counts: { negative: 0, positive: 0, not_stated: 0 },
  outcome_counts: { supported: 0, tested_absent: 0, inconclusive: 0, not_significant: 0 },
  independent_outcome_counts: { supported: 0, tested_absent: 0, inconclusive: 0, not_significant: 0 },
  assertion_count: 0,
  evidence_count: 0,
  distinct: { publications: 0, releases: 0, datasets: 0 },
  by_basis: {},
})

/** The graphs whose legends the cross-cutting rows check: every recording and each hand-written channel. */
function legendGraphs(): [string, TypedGraph, boolean | undefined][] {
  return [
    ...RECORDED_NETWORKS.map((name): [string, TypedGraph, boolean | undefined] => [name, network(name), undefined]),
    ['network-all-edges, non-support hidden', network('network-all-edges'), false],
    ['hand-written channels', handWritten(
      roleOrderedEdge, directionOnlyEdge, notSignificantEdge, inconclusiveEdge, negativeSignEdge, positiveSignEdge,
      mixedSignEdge, levelAssignedEdge, bareNonSupportEdge,
    ), true],
    ['the unspecified edge', handWritten(unspecifiedEdge), undefined],
    ['unknown values', handWritten(unknownChannelsEdge, unknownLevelEdge), undefined],
  ]
}

describe('legendEntries: which channels are listed', () => {
  it('lists network-tf1 without a tested-absent line, since the API hid relationship:7', () => {
    expect(keys(network('network-tf1'), { vocabularies: vocabularies() })).toEqual([
      'in-view', 'marker:tee', 'marker:vee-hollow', 'family:regulation', 'node:gene', 'seed', 'synthetic',
    ])
  })

  it('lists every recorded channel of network-all-edges, the tested-absent line only while it is drawn', () => {
    const graph = network('network-all-edges')
    expect(defaultShowNonSupporting(graph)).toBe(true)
    expect(keys(graph, { vocabularies: vocabularies() })).toEqual([
      'in-view', 'marker:triangle', 'marker:tee', 'marker:diamond', 'marker:vee-hollow', 'marker:symmetric',
      'line:tested-absent', 'line:predicted', 'width', 'level', 'family:physical', 'family:regulation',
      'node:gene', 'node:protein', 'seed', 'synthetic',
    ])
    const hidden = keys(graph, { vocabularies: vocabularies(), showNonSupporting: false })
    expect(hidden).not.toContain('line:tested-absent')
    expect(hidden).toContain('line:predicted')
  })

  it('lists network-p1-reference with the stating release, the physical family and level and the isoform, never family:reference', () => {
    const entries = legendEntries(network('network-p1-reference'), { vocabularies: vocabularies() })
    expect(entries.map((item) => item.key)).toEqual([
      'in-view', 'marker:symmetric', 'reference', 'level', 'family:physical',
      'node:gene', 'node:protein', 'node:protein_isoform', 'seed', 'synthetic',
    ])
    expect(entry(entries, 'reference').text).toBe(
      `${LEGEND_TEXT.reference} Encodes links here are stated by Synthetic regulation example v2.`
      + ' Isoform of links here are stated by Synthetic regulation example v2.',
    )
    expect(entry(entries, 'level').text).toBe(
      'Physical interactions are labelled with the strongest level stated by their supporting results in view: direct interaction.',
    )
    expect(entry(entries, 'family:physical').text).toBe('Bluish green line: Physical interaction.')
    expect(entry(entries, 'node:protein_isoform').text).toBe('Protein isoform: diamond with rounded corners.')
  })

  it('lists no family for the tested-absent recordings, whose only edges are grey', () => {
    expect(keys(network('network-tf1-tested-absent'))).toEqual(['in-view', 'line:tested-absent', 'node:gene', 'seed', 'synthetic'])
    expect(keys(network('network-p2-tested-absent'))).toEqual(['in-view', 'line:tested-absent', 'node:protein', 'seed', 'synthetic'])
  })

  it('names a superseded release when the graph has one', () => {
    for (const name of ['network-tf1-v1', 'network-tf1-both-releases']) {
      expect(entry(legendEntries(network(name)), 'superseded').text).toBe(
        'Includes Synthetic regulation example v1 (superseded), which a newer release has since corrected. It is shown because it was selected, and a mixed effect may be its correction.',
      )
    }
    expect(keys(network('network-tf1'))).not.toContain('superseded')
  })

  it('lists nothing for no graph or an empty one', () => {
    expect(legendEntries(null)).toEqual([])
    expect(legendEntries(EMPTY_GRAPH)).toEqual([])
  })

  it('lists a reference pair listed only for a tested-absent result as that result, with no release stating it', () => {
    const response = makeNetworkResponse({ edges: [testedAbsentReferenceEdge] })
    const graph = graphOfNetwork({ ...response, query: { ...response.query, outcome: ['tested_absent'] } })
    const entries = legendEntries(graph, { vocabularies: vocabularies() })
    expect(entries.map((item) => item.key)).toEqual(['in-view', 'line:tested-absent', 'node:gene', 'seed', 'synthetic'])
    expect(entries.filter((item) => item.text.includes('stated by'))).toEqual([])
  })

  it('lists nothing that only an element the canvas cannot draw would show', () => {
    const response = makeNetworkResponse()
    const testedAbsent = makeGraphEdge({
      id: 'relationship:997', target: 'entity:997', effect: null,
      effect_counts: { activating: 0, inhibiting: 0, unknown: 0, not_stated: 0 },
      outcome_counts: { supported: 0, tested_absent: 1, inconclusive: 0, not_significant: 0 },
      independent_outcome_counts: { supported: 0, tested_absent: 1, inconclusive: 0, not_significant: 0 },
      assertion_count: 0, evidence_count: 0, distinct: { publications: 0, releases: 0, datasets: 0 }, by_basis: {},
    })
    const graph = graphOfNetwork({
      ...response,
      nodes: [...response.nodes, makeNetworkNode({ id: 'entity:902', type: 'protein' })],
      edges: [
        ...response.edges, testedAbsent, { ...roleOrderedEdge, target: 'entity:998' },
        { ...directionOnlyEdge, id: response.edges[0].id }, { ...negativeSignEdge, id: 'constructor' },
      ],
    })
    expect(keptGraph(graph).dropped.map((item) => item.reason)).toEqual([
      'duplicate-id', 'missing-endpoint', 'missing-endpoint', 'duplicate-id', 'reserved-id',
    ])
    for (const showNonSupporting of [undefined, true, false]) {
      expect(legendEntries(graph, { vocabularies: vocabularies(), showNonSupporting }), String(showNonSupporting))
        .toEqual(legendEntries(graphOfNetwork(response), { vocabularies: vocabularies(), showNonSupporting }))
    }
  })

  it('lists the entity types by label, whatever order their nodes arrive in', () => {
    const response = makeNetworkResponse()
    const [seed, partner] = response.nodes
    const graph = graphOfNetwork({ ...response, nodes: [{ ...partner, type: 'protein' }, seed] })
    for (const vocab of [vocabularies(), null]) {
      expect(keys(graph, { vocabularies: vocab }).filter((key) => key.startsWith('node:'))).toEqual(['node:gene', 'node:protein'])
    }
  })

  it('lists the hand-written channels the demonstration cannot produce', () => {
    const vocab = { ...vocabularies(), relationship_types: [...vocabularies().relationship_types, ligandReceptorType] }
    const graph = handWritten(
      roleOrderedEdge, directionOnlyEdge, notSignificantEdge, inconclusiveEdge, negativeSignEdge, positiveSignEdge,
      mixedSignEdge, levelAssignedEdge,
    )
    const entries = legendEntries(graph, { vocabularies: vocab, showNonSupporting: true })
    expect(entries.map((item) => item.key)).toEqual([
      'in-view', 'marker:circle:syn_ligand_receptor', 'marker:square-direction', 'marker:symmetric',
      'line:not-significant', 'line:inconclusive', 'level-assigned', 'sign', 'family:physical', 'family:regulation',
      'node:gene', 'seed', 'synthetic',
    ])
    expect(entry(entries, 'marker:circle:syn_ligand_receptor')).toEqual({
      key: 'marker:circle:syn_ligand_receptor',
      swatch: { kind: 'edge', marker: 'circle', line: 'supported', colour: NEUTRAL },
      text: 'Circle at the receptor end: Synthetic ligand and receptor, two fixed roles, from ligand to receptor, with no claim of cause or effect.',
    })
    // Without role labels the sentence still reads, from the slug.
    expect(entry(legendEntries(handWritten(roleOrderedEdge)), 'marker:circle:syn_ligand_receptor').text).toBe(
      'Circle at the end of the second role: syn ligand receptor, two fixed roles, from the first role to the second, with no claim of cause or effect.',
    )
    // Hidden by default: this graph answers the default request.
    expect(keys(graph, { vocabularies: vocab })).not.toContain('line:not-significant')
  })

  it('lists no level for the unspecified type, and no sentence calls it a physical interaction', () => {
    const entries = legendEntries(handWritten(unspecifiedEdge), { vocabularies: vocabularies() })
    expect(entries.map((item) => item.key)).not.toContain('level')
    expect(entries.map((item) => item.key)).toContain('family:physical')
    expect(entries.filter((item) => /physical interaction/i.test(item.text))).toEqual([])
  })

  it('says "in view" in every sentence that summarises the evidence', () => {
    const entries = [
      ...legendEntries(network('network-all-edges'), { vocabularies: vocabularies() }),
      ...legendEntries(handWritten(bareNonSupportEdge), { showNonSupporting: true }),
    ]
    const summaries = [
      'in-view', 'marker:triangle', 'marker:tee', 'marker:diamond', 'marker:vee-hollow', 'line:non-support',
      'line:predicted', 'width', 'level',
    ]
    for (const key of summaries) expect(entry(entries, key).text, key).toContain('in view')
  })

  it('gives every edge swatch a cue besides colour; the symmetric one says it has no end marker', () => {
    for (const [name, graph, show] of legendGraphs()) {
      for (const item of legendEntries(graph, { vocabularies: vocabularies(), showNonSupporting: show })) {
        const { swatch } = item
        if (swatch.kind !== 'edge') continue
        if (item.key === 'marker:symmetric') {
          expect(swatch, name).toEqual({ kind: 'edge', marker: 'none', line: 'supported', colour: NEUTRAL })
          expect(item.text, name).toContain('no end marker')
          continue
        }
        expect(swatch.marker !== 'none' || swatch.line !== 'supported' || swatch.width !== undefined, `${name} ${item.key}`).toBe(true)
      }
    }
  })

  it('lists level, level-assigned and sign exactly when edgeLabel draws such a label on a visible edge', () => {
    const levels = Object.values(LEVEL_WORDS)
    const signs = Object.values(SIGN_WORDS)
    for (const [name, graph, show] of legendGraphs()) {
      const kept = keptGraph(graph).graph
      const shown = visibleGraph(kept, { showNonSupporting: show ?? defaultShowNonSupporting(graph) })
      const labels = shown.edges.map((edge) => edgeLabel(edge, kept, vocabularies()))
      const listed = keys(graph, { vocabularies: vocabularies(), showNonSupporting: show })
      const drawn = levels.filter((words) => labels.includes(words))
      expect(listed.includes('level'), `${name} level`).toBe(drawn.length > 0)
      expect(listed.includes('level-assigned'), `${name} level-assigned`).toBe(labels.includes(LEVEL_ASSIGNED_WORDS))
      expect(listed.includes('sign'), `${name} sign`).toBe(labels.some((label) => signs.includes(label)))
      if (drawn.length > 0) {
        // Only the strings drawn.
        const text = entry(legendEntries(graph, { vocabularies: vocabularies(), showNonSupporting: show }), 'level').text
        expect(levels.filter((words) => text.includes(words)), name).toEqual(drawn)
      }
    }
  })

  it("begins every recorded edge's description with its type label", () => {
    const entity = recorded<GraphEntityResponse>('entity-tf1').body
    const graphs = [
      ...RECORDED_NETWORKS.map(network),
      { ...EMPTY_GRAPH, nodes: entity.nodes, edges: entity.reference_relationships, releases: entity.releases },
    ]
    for (const vocab of [vocabularies(), null]) {
      for (const graph of graphs) {
        for (const edge of graph.edges) {
          expect(edgeDescription(edge, graph, null, vocab).startsWith(typeLabel(edge.type, vocab)), edge.id).toBe(true)
        }
      }
    }
  })
})

describe('the reference sentence', () => {
  it('names no release that the graph does not hold', () => {
    const text = entry(legendEntries(handWritten(unresolvedReferenceEdge)), 'reference').text
    expect(text).toBe(LEGEND_TEXT.reference)
    expect(text).not.toContain('stated by')
  })

  it('names a release once for two links of one type that cite it', () => {
    const p1 = network('network-p1-reference')
    const encodes = p1.edges.find((edge) => edge.id === 'relationship:1') as GraphEdge
    const second = { ...encodes, id: 'relationship:916', source: 'entity:12' }
    const graph: TypedGraph = { ...p1, edges: [encodes, second] }
    const text = entry(legendEntries(graph, { vocabularies: vocabularies() }), 'reference').text
    expect(text.split('Synthetic regulation example v2')).toHaveLength(2)
    expect(text).toBe(`${LEGEND_TEXT.reference} Encodes links here are stated by Synthetic regulation example v2.`)
  })

  it('names no release for a type one of whose links is also listed with a release that reports it only as tested and not detected', () => {
    const response = makeNetworkResponse()
    const everyOutcome = (...edges: GraphEdge[]) => graphOfNetwork({
      ...response, query: { ...response.query, outcome: ['all'] }, edges, releases: [...response.releases, testedAbsentRelease],
    })
    const supportedOnly = { supported: 1, tested_absent: 0, inconclusive: 0, not_significant: 0 }
    const stated: GraphEdge = {
      ...mixedReferenceEdge, id: 'relationship:918', type: 'isoform_of', releases: ['release:901'],
      outcome_counts: supportedOnly, independent_outcome_counts: { ...supportedOnly },
    }
    const text = (...edges: GraphEdge[]) => entry(legendEntries(everyOutcome(...edges), { vocabularies: vocabularies() }), 'reference').text
    // A link of another type that its one release states keeps its sentence.
    expect(text(mixedReferenceEdge, stated)).toBe(`${LEGEND_TEXT.reference} Isoform of links here are stated by Synthetic extras x1.`)
    // One of the same type does not tell which release states the other, so that type names none.
    expect(text(mixedReferenceEdge, { ...stated, type: 'encodes' })).toBe(LEGEND_TEXT.reference)
  })
})

describe("the legend's words", () => {
  it('are the sentences of the specification, word for word', () => {
    // Copied from the specification, never from legendText.ts, so that a changed word fails here.
    expect(LEGEND_TEXT).toEqual({
      'in-view':
        "Markers, line styles, widths and labels describe the evidence in view: the selected releases' results that pass the current filters.",
      'marker:triangle':
        'Arrowhead (filled triangle): activates. At least one supporting result in view states activation, and none in view states inhibition.',
      'marker:tee':
        "Bar (tee), labelled 'inhibits': inhibits. At least one supporting result in view states inhibition, and none in view states activation. Not the same as tested and not detected.",
      'marker:diamond':
        "Diamond: mixed effect. Among the supporting results in view, some state activation and others inhibition; the relationship's panel counts each.",
      'marker:vee-hollow':
        'Open arrowhead (hollow V): effect unknown. Every supporting result in view states the effect as unknown, or states none.',
      'marker:square-direction':
        'Filled square at the target end: direction only. The type states a direction but no effect.',
      'marker:symmetric':
        'Plain line, no end marker: a symmetric relationship, the same claim whichever way it is read.',
      reference:
        "Thin grey line with a chevron and a label: a reference link between records, not evidence of interaction or regulation. The label names the link's type and the release that states it, or only the type when another release in view reports the link without supporting it. The gene, the protein entry and its isoforms are separate records.",
      'line:tested-absent':
        "Dashed: tested and not detected, which is not the same as inhibition. It has no end marker; the relationship's panel gives its direction.",
      'line:not-significant':
        "Thin grey line with a small open square at its middle, labelled 'not significant at the stated threshold': measured, but it did not meet the source's threshold, so neither support nor absence.",
      'line:inconclusive':
        "Thin grey line with a small open square at its middle, labelled 'inconclusive': the source itself calls the test inconclusive.",
      'line:non-support':
        'Thin grey line with a small open square at its middle and no label: listed without supporting evidence in view.',
      'line:predicted':
        'Dotted: every supporting result in view is a computational prediction; none in view is an experiment.',
      width:
        'Thicker line: more distinct publications report the supporting results in view, with 5 or more drawn alike. A thin line can mean that no publication is recorded, not that the evidence is weak.',
      'level-assigned':
        "'Co-complex association, assigned by openPIP from the stated assay': the source states no interaction type; from its co-complex purification assay openPIP assigned the lowest level, association, which never means binding.",
      sign:
        "Labelled negative (aggravating), positive (alleviating) or mixed sign: a genetic interaction's sign, relative to the score's stated null model. Not inhibition, and not a tested-and-not-detected result.",
      seed: 'Two-tone outline: a starting entity of this network (a seed).',
      synthetic: 'Synthetic demonstration data: made up to demonstrate openPIP; it describes no real biology.',
    })
    expect(LEGEND_HEADING).toBe(
      "Colour shows the kind of relationship. Arrowheads and line styles show effect and evidence, and every relationship's panel names its type.",
    )
    expect(SHAPE_WORDS).toEqual({
      gene: 'rectangle rounded at the bottom',
      protein: 'ellipse',
      isoform: 'diamond with rounded corners',
      other: 'triangle with rounded corners',
    })
  })
})

describe('values a deploy brings early', () => {
  it('print neither "undefined" nor "[object" in the entries or the rendered legend', () => {
    const graph = handWritten(unknownChannelsEdge, unknownLevelEdge)
    for (const vocab of [vocabularies(), null]) {
      const entries = legendEntries(graph, { vocabularies: vocab })
      expect(entries.length).toBeGreaterThan(0)
      for (const item of entries) {
        expect(item.text, item.key).not.toMatch(/undefined|\[object/)
        expect(item.key, item.key).not.toMatch(/undefined|\[object/)
      }
      const view = render(<TypedLegend graph={graph} vocabularies={vocab} />)
      expect(view.container.textContent).not.toMatch(/undefined|\[object/)
      view.unmount()
    }
  })
})

describe('TypedLegend', () => {
  it("shows exactly the entries' texts in order, each beside a swatch hidden from screen readers, in a list named by the title", () => {
    const graph = network('network-all-edges')
    const view = render(<TypedLegend graph={graph} vocabularies={vocabularies()} />)
    const list = view.getByRole('list', { name: 'Legend' })
    const items = Array.from(list.querySelectorAll('li'))
    expect(items.map((item) => item.textContent)).toEqual(
      legendEntries(graph, { vocabularies: vocabularies() }).map((item) => item.text),
    )
    for (const item of items) {
      const swatch = item.querySelector('svg')
      expect(swatch?.getAttribute('aria-hidden')).toBe('true')
      expect(swatch?.getAttribute('focusable')).toBe('false')
    }
    // The heading comes before the list, since the list holds family entries.
    expect(view.getByText(LEGEND_HEADING).compareDocumentPosition(list) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(view.container.querySelector('[class]')).toBeNull()
  })

  it('shows the heading only when a family entry is listed', () => {
    const view = render(<TypedLegend graph={network('network-tf1-tested-absent')} />)
    expect(view.queryByText(LEGEND_HEADING)).toBeNull()
    expect(view.getByRole('list', { name: 'Legend' })).toBeTruthy()
  })

  it('renders text from the vocabularies as text, never as markup', () => {
    const bold = {
      ...vocabularies(),
      relationship_types: vocabularies().relationship_types.map((type) =>
        type.slug === 'transcriptional_regulation' ? { ...type, label: '<b>Regulation</b>' } : type,
      ),
    }
    const view = render(<TypedLegend graph={network('network-tf1')} vocabularies={bold} />)
    expect(view.getByText('Blue line: <b>Regulation</b>.')).toBeTruthy()
    expect(view.container.querySelector('b')).toBeNull()
  })

  it('renders nothing for no graph', () => {
    const view = render(<TypedLegend graph={null} />)
    expect(view.container.innerHTML).toBe('')
  })

  it('keeps two legends on one page apart under their titles', () => {
    const view = render(
      <>
        <TypedLegend graph={network('network-tf1')} vocabularies={vocabularies()} title="Regulation legend" />
        <TypedLegend graph={network('network-p1-reference')} vocabularies={vocabularies()} title="Reference legend" />
      </>,
    )
    const left = view.getByRole('list', { name: 'Regulation legend' })
    const right = view.getByRole('list', { name: 'Reference legend' })
    expect(left.getAttribute('aria-labelledby')).not.toBe(right.getAttribute('aria-labelledby'))
    expect(right.textContent).toContain('Encodes links here are stated by')
    expect(left.textContent).not.toContain('Encodes links')
  })

  it('draws each line swatch at its canvas width, dash and opacity, with the middle square on the thin grey lines', () => {
    const all = swatchesOf(network('network-all-edges'))
    const thin = swatchesOf(handWritten(notSignificantEdge, bareNonSupportEdge), true)
    const strokes = (svg: SVGSVGElement | undefined) =>
      Array.from(svg?.querySelectorAll('line') ?? []).map((line) => [line.getAttribute('stroke-width'), line.getAttribute('stroke-dasharray')])

    // One publication's width from the channel table: 1.5 for the coloured and the dashed lines, 1 for the thin grey ones.
    expect(strokes(all.get('line:tested-absent'))).toEqual([['1.5', '6 3']])
    expect(all.get('line:tested-absent')?.querySelector('g')?.getAttribute('opacity')).toBe(String(TESTED_ABSENT_OPACITY))
    expect(strokes(all.get('line:predicted'))).toEqual([['1.5', '1 1']])
    expect(strokes(all.get('marker:symmetric'))).toEqual([['1.5', null]])
    expect(strokes(all.get('width'))).toEqual([['5.5', null]])
    for (const key of ['line:not-significant', 'line:non-support']) {
      expect(strokes(thin.get(key)), key).toEqual([['1', null], ['1', null]])
      expect(thin.get(key)?.querySelector('rect')?.getAttribute('fill'), key).toBe('none')
    }
    expect(all.get('marker:tee')?.querySelector('polygon')).not.toBeNull()
    expect(all.get('node:gene')?.querySelector('path')).not.toBeNull()
    expect(all.get('seed')?.querySelectorAll('ellipse')).toHaveLength(3)
    expect(all.get('family:regulation')?.querySelector('rect')?.getAttribute('fill')).toBe('#0072B2')
    expect(all.get('in-view')?.childElementCount).toBe(0)
  })

  it('draws a line swatch without its own width as wide as the canvas draws that line at one publication', () => {
    const lines = ['supported', 'predicted', 'tested-absent', 'not-significant', 'inconclusive', 'non-support', 'reference'] as const
    expect(lines.map(lineSwatchWidthPx)).toEqual([1.5, 1.5, 1.5, 1, 1, 1, 1])
  })
})

describe('noticeLines and TypedNetworkNotice', () => {
  it('word the recorded pages with exact counts', () => {
    expect(noticeLines(network('network-tf1'))).toEqual([
      '1 relationship with tested-and-not-detected evidence is hidden by the outcome filter.',
    ])
    expect(noticeLines(network('network-tf1-tested-absent'))).toEqual([
      '3 relationships with supporting evidence are hidden by the outcome filter.',
    ])
    expect(noticeLines(network('network-truncated'))).toEqual([
      '1 relationship shown; 3 more that matched were left out to keep the network within its size limits (the relationship limit).',
    ])
    expect(noticeLines(network('network-p2-inconclusive'))).toEqual([
      '2 relationships with supporting evidence are hidden by the outcome filter.',
      '1 relationship with tested-and-not-detected evidence is hidden by the outcome filter.',
      'A relationship with evidence of two kinds is counted under each.',
    ])
    expect(noticeLines(network('network-all-edges'))).toEqual([])
    expect(noticeLines(network('network-all-edges'), { showNonSupporting: false })).toEqual([
      '1 relationship listed without supporting evidence is hidden on the canvas.',
    ])
    expect(noticeLines(null)).toEqual([])
  })

  it('word hand-written pages, by warning codes only, and the dropped elements', () => {
    const base = graphOfNetwork(makeNetworkResponse())
    const page = base.page as NonNullable<TypedGraph['page']>
    expect(noticeLines({ ...base, page: { ...page, truncated: true, omitted: { candidates: 1 } } })).toEqual([
      '1 lookup found more relationships than are ranked per seed; only the most covered were considered.',
    ])
    expect(noticeLines({ ...base, page: { ...page, truncated: true, omitted: { candidates: 2 } } })).toEqual([
      '2 lookups found more relationships than are ranked per seed; only the most covered were considered.',
    ])
    const cut = { ...base, page: { ...page, truncated: true, omitted: { edges: 3 } } }
    expect(noticeLines({ ...cut, warnings: [{ code: 'nodes_truncated', detail: 'Ignored: 1 relationship left.' }] })).toEqual([
      '1 relationship shown; 3 more that matched were left out to keep the network within its size limits (the entity limit).',
    ])
    expect(noticeLines({
      ...cut,
      warnings: [{ code: 'edges_truncated', detail: '' }, { code: 'nodes_truncated', detail: '' }],
    })).toEqual([
      '1 relationship shown; 3 more that matched were left out to keep the network within its size limits (the relationship and entity limits).',
    ])
    expect(noticeLines({
      ...base, page: { ...page, hidden: { supported: 0, tested_absent: 3, inconclusive: 1, not_significant: 2 } },
    })).toEqual([
      '3 relationships with tested-and-not-detected evidence are hidden by the outcome filter.',
      '1 relationship with inconclusive evidence is hidden by the outcome filter.',
      '2 relationships with not-significant evidence are hidden by the outcome filter.',
      'A relationship with evidence of two kinds is counted under each.',
    ])

    const edge = base.edges[0]
    // A dropped edge is neither shown nor counted as shown.
    expect(noticeLines({ ...cut, edges: [...base.edges, { ...edge, id: 'relationship:998', target: 'entity:998' }] })).toEqual([
      '1 relationship shown; 3 more that matched were left out to keep the network within its size limits.',
      '1 relationship could not be drawn: an entity it names is missing.',
    ])
    expect(noticeLines({ ...base, edges: [...base.edges, { ...edge, id: 'relationship:998', target: 'entity:998' }] })).toEqual([
      '1 relationship could not be drawn: an entity it names is missing.',
    ])
    expect(noticeLines({
      ...base,
      edges: [
        ...base.edges,
        { ...edge, id: 'relationship:998', target: 'entity:998' },
        { ...edge, id: 'relationship:999', source: 'entity:999' },
        { ...edge, id: 'constructor' },
        { ...edge },
      ],
    })).toEqual([
      '2 relationships could not be drawn: an entity they name is missing.',
      '2 elements with a repeated or reserved id were left out.',
    ])
  })

  it('counts as shown only the relationships the canvas draws, and words the ones it hides', () => {
    const response = makeNetworkResponse({ edges: [makeGraphEdge(), notSignificantEdge] })
    const graph = graphOfNetwork({ ...response, page: { ...response.page, truncated: true, omitted: { edges: 3 } } })
    expect(noticeLines(graph, { showNonSupporting: false })).toEqual([
      '1 relationship shown; 3 more that matched were left out to keep the network within its size limits.',
      '1 relationship listed without supporting evidence is hidden on the canvas.',
    ])
    expect(noticeLines(graph, { showNonSupporting: true })).toEqual([
      '2 relationships shown; 3 more that matched were left out to keep the network within its size limits.',
    ])
  })

  it('keeps one live region, rendered before it has anything to say', () => {
    const view = render(<TypedNetworkNotice graph={null} />)
    const region = view.getByRole('status')
    expect(region.getAttribute('aria-live')).toBe('polite')
    expect(region.childElementCount).toBe(0)
    view.rerender(<TypedNetworkNotice graph={network('network-tf1')} />)
    expect(view.getByRole('status')).toBe(region)
    expect(Array.from(region.querySelectorAll('p')).map((line) => line.textContent)).toEqual(noticeLines(network('network-tf1')))
    view.rerender(<TypedNetworkNotice graph={network('network-all-edges')} showNonSupporting={false} />)
    expect(view.getByRole('status')).toBe(region)
    expect(region.textContent).toBe('1 relationship listed without supporting evidence is hidden on the canvas.')
    view.rerender(<TypedNetworkNotice graph={null} />)
    expect(view.getByRole('status')).toBe(region)
    expect(region.childElementCount).toBe(0)
  })
})
