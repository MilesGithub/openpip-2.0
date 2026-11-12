import type { GraphEdge, GraphFamily, GraphOutcome, GraphVocabularies } from '../types'
import { edgeClasses, edgeLine, edgeMarker, edgeWidthPx, isNonSupport, nodeShape, nonSupportOutcome } from '../network/channels'
import type { EdgeLine, EdgeMarker } from '../network/channels'
import { GRAPH_OUTCOMES, keptGraph, own } from '../network/graph'
import type { TypedGraph } from '../network/graph'
import { FAMILY_COLOURS, NEUTRAL, familyColour } from '../network/palette'
import { defaultShowNonSupporting, visibleGraph } from '../network/visibility'
import {
  LEVEL_ASSIGNED_WORDS, LEVEL_WORDS, edgeLabel, entityTypeLabel, releaseWords, roleLabels, statedByEachRelease,
  statingReleases, typeLabel,
} from '../network/words'
import { LEGEND_TEXT, circleText, familyText, levelText, nodeText, referenceText, supersededText } from './legendText'

export type LegendSwatch =
  | { kind: 'edge'; marker: EdgeMarker; line: EdgeLine; colour: string; width?: number }
  | { kind: 'node'; shape: 'gene' | 'protein' | 'isoform' | 'other'; seed: boolean }
  | { kind: 'colour'; colour: string }
  | { kind: 'none' }
export interface LegendEntry { key: string; swatch: LegendSwatch; text: string }

/** In types.ts's declaration order, the legend's order of families; a record, so that tsc fails when the union changes. */
const FAMILIES: Readonly<Record<GraphFamily, true>> = { physical: true, regulation: true, rna: true, genetic: true, modification: true, ligand_receptor: true, association: true, phenotype: true, membership: true, reference: true }

const EFFECT_MARKERS = ['triangle', 'tee', 'diamond', 'vee-hollow'] as const
const LINES = ['tested-absent', 'not-significant', 'inconclusive', 'non-support', 'predicted'] as const
/** The widest line drawn, at five publications or more. */
const WIDEST_PX = 5.5

function distinct<T>(values: readonly T[]): T[] {
  return Array.from(new Set(values))
}

function edgeSwatch(marker: EdgeMarker, line: EdgeLine, colour: string = NEUTRAL): LegendSwatch {
  return { kind: 'edge', marker, line, colour }
}

const NO_COUNTS: Readonly<Record<GraphOutcome, number>> = { supported: 0, tested_absent: 0, inconclusive: 0, not_significant: 0 }
const LINE_OUTCOMES: Readonly<Record<string, GraphOutcome>> = {
  supported: 'supported', predicted: 'supported', reference: 'supported',
  'tested-absent': 'tested_absent', 'not-significant': 'not_significant', inconclusive: 'inconclusive',
}

/**
 * A one-publication edge the channel table draws with `line`, so that a swatch without
 * its own width is as wide as edgeWidthPx draws that line, and the two cannot drift.
 */
function oneEdgeOf(line: EdgeLine): GraphEdge {
  const outcome = own(LINE_OUTCOMES, line)
  const counts = outcome === undefined ? { ...NO_COUNTS } : { ...NO_COUNTS, [outcome]: 1 }
  const supported = outcome === 'supported'
  return {
    id: 'legend', type: 'legend', family: line === 'reference' ? 'reference' : 'association', orientation: 'symmetric',
    source: 'legend', target: 'legend', releases: [], is_synthetic: false, qualifiers: {}, shown: true,
    effect: null, effect_counts: { activating: 0, inhibiting: 0, unknown: 0, not_stated: 0 },
    genetic_sign: null, sign_counts: { negative: 0, positive: 0, not_stated: 0 },
    outcome_counts: counts, independent_outcome_counts: counts, assertion_count: supported ? 1 : 0, evidence_count: 1,
    distinct: { publications: 1, releases: 1, datasets: 1 },
    by_basis: line === 'predicted' ? { computational_prediction: 1 } : supported ? { experimental: 1 } : {},
    level: null, level_counts: {}, level_assigned: null, tested_levels: {}, outside_filters: 0,
    outside_outcome_counts: { ...NO_COUNTS },
  }
}

/** A swatch's width for `line` when it has none of its own: here, in a strict module, so a stale field fails the strict test. */
export function lineSwatchWidthPx(line: EdgeLine): number {
  return edgeWidthPx(oneEdgeOf(line), GRAPH_OUTCOMES)
}

/**
 * Whether edgeLabel draws this edge's label by its level rule: physical with a level, and
 * none of the reference, non-support and unspecified-type rules before it applies.
 */
function labelledByLevel(edge: GraphEdge, requested: readonly GraphOutcome[]): boolean {
  if (edge.family !== 'physical' || edge.level === null || edge.type === 'protein_interaction_unspecified') return false
  return !(isNonSupport(edge) && nonSupportOutcome(edge, requested) !== null)
}

/**
 * Built from what the channel and word functions compute for the visible elements, never
 * read back from Cytoscape and never from count keys, so the legend lists what is drawn.
 */
export function legendEntries(graph: TypedGraph | null, options?: { showNonSupporting?: boolean;
  vocabularies?: GraphVocabularies | null }): LegendEntry[] {
  if (graph === null || graph === undefined) return []
  const kept = keptGraph(graph).graph
  const visible = visibleGraph(kept, { showNonSupporting: options?.showNonSupporting ?? defaultShowNonSupporting(graph) })
  const vocabularies = options?.vocabularies
  const requested = kept.requestedOutcomes
  const drawn = visible.edges.map((edge) => ({
    edge,
    marker: edgeMarker(edge),
    line: edgeLine(edge, requested),
    label: edgeLabel(edge, kept, vocabularies),
  }))
  const markers = new Set(drawn.map((item) => item.marker))
  const lines = new Set(drawn.map((item) => item.line))
  const coloured = drawn.filter((item) => item.line === 'supported' || item.line === 'predicted')

  const edgeEntries: LegendEntry[] = []
  for (const marker of EFFECT_MARKERS) {
    const text = own(LEGEND_TEXT, `marker:${marker}`)
    if (markers.has(marker) && text !== undefined) edgeEntries.push({ key: `marker:${marker}`, swatch: edgeSwatch(marker, 'supported'), text })
  }
  const circles = new Map<string, GraphFamily>()
  for (const item of drawn) {
    if (item.marker === 'circle' && !circles.has(item.edge.type)) circles.set(item.edge.type, item.edge.family)
  }
  for (const [type, family] of circles) {
    edgeEntries.push({
      key: `marker:circle:${type}`,
      swatch: edgeSwatch('circle', 'supported', familyColour(family)),
      text: circleText(typeLabel(type, vocabularies), roleLabels(type, vocabularies)),
    })
  }
  if (markers.has('square-direction')) {
    edgeEntries.push({
      key: 'marker:square-direction',
      swatch: edgeSwatch('square-direction', 'supported'),
      text: LEGEND_TEXT['marker:square-direction'],
    })
  }
  if (coloured.some((item) => item.edge.orientation === 'symmetric')) {
    edgeEntries.push({ key: 'marker:symmetric', swatch: edgeSwatch('none', 'supported'), text: LEGEND_TEXT['marker:symmetric'] })
  }
  const references = drawn.filter((item) => item.marker === 'chevron-reference').map((item) => item.edge)
  if (references.length > 0) {
    const stated = distinct(references.map((edge) => edge.type)).map((type) => {
      const links = references.filter((edge) => edge.type === type)
      // A link listed with a release that does not state it leaves unknown which releases state
      // this type's links, and naming the other links' releases could name too few or the wrong ones.
      const found = new Set(links.every(statedByEachRelease) ? links.flatMap((edge) => statingReleases(edge, kept)) : [])
      return {
        typeLabel: typeLabel(type, vocabularies),
        releases: kept.releases.filter((release) => found.has(release)).map(releaseWords),
      }
    })
    edgeEntries.push({ key: 'reference', swatch: edgeSwatch('chevron-reference', 'reference'), text: referenceText(stated) })
  }
  for (const line of LINES) {
    const text = own(LEGEND_TEXT, `line:${line}`)
    if (lines.has(line) && text !== undefined) edgeEntries.push({ key: `line:${line}`, swatch: edgeSwatch('none', line), text })
  }
  if (coloured.some((item) => item.edge.distinct.publications >= 2)) {
    edgeEntries.push({
      key: 'width',
      swatch: { kind: 'edge', marker: 'none', line: 'supported', colour: NEUTRAL, width: WIDEST_PX },
      text: LEGEND_TEXT.width,
    })
  }
  const byLevel = drawn.filter((item) => labelledByLevel(item.edge, requested))
  const levelLabels = new Set(byLevel.filter((item) => item.edge.level_assigned !== true).map((item) => item.label))
  // Only the D20 strings drawn, in D20's own order (Y19).
  const levels = Object.values(LEVEL_WORDS).filter((words) => levelLabels.has(words))
  if (levels.length > 0) edgeEntries.push({ key: 'level', swatch: { kind: 'none' }, text: levelText(levels) })
  if (byLevel.some((item) => item.edge.level_assigned === true && item.label === LEVEL_ASSIGNED_WORDS)) {
    edgeEntries.push({ key: 'level-assigned', swatch: { kind: 'none' }, text: LEGEND_TEXT['level-assigned'] })
  }
  if (drawn.some((item) => edgeClasses(item.edge, requested).some((name) => name.startsWith('gsign-')))) {
    edgeEntries.push({ key: 'sign', swatch: { kind: 'none' }, text: LEGEND_TEXT.sign })
  }
  // Never for reference links or a family drawn grey: no line is drawn in their colour.
  for (const family of Object.keys(FAMILIES)) {
    const colour = own(FAMILY_COLOURS, family)
    if (family === 'reference' || colour === undefined) continue
    const types = distinct(coloured.filter((item) => item.edge.family === family).map((item) => typeLabel(item.edge.type, vocabularies)))
    if (types.length > 0) edgeEntries.push({ key: `family:${family}`, swatch: { kind: 'colour', colour }, text: familyText(colour, types) })
  }

  const entries: LegendEntry[] = []
  if (edgeEntries.length > 0) entries.push({ key: 'in-view', swatch: { kind: 'none' }, text: LEGEND_TEXT['in-view'] }, ...edgeEntries)
  const entityTypes = distinct(visible.nodes.map((node) => node.type))
    .map((type) => ({ type, label: entityTypeLabel(type, vocabularies) }))
    .sort((a, b) => a.label.localeCompare(b.label))
  for (const { type, label } of entityTypes) {
    const node = visible.nodes.find((candidate) => candidate.type === type)
    const shape = node === undefined ? 'other' : nodeShape(node)
    entries.push({ key: `node:${type}`, swatch: { kind: 'node', shape, seed: false }, text: nodeText(label, shape) })
  }
  if (visible.nodes.some((node) => node.is_seed === true)) {
    entries.push({ key: 'seed', swatch: { kind: 'node', shape: 'protein', seed: true }, text: LEGEND_TEXT.seed })
  }
  if (visible.edges.some((edge) => edge.is_synthetic) || kept.releases.some((release) => release.is_synthetic)) {
    entries.push({ key: 'synthetic', swatch: { kind: 'none' }, text: LEGEND_TEXT.synthetic })
  }
  const superseded = kept.releases.filter((release) => release.superseded)
  if (superseded.length > 0) {
    entries.push({ key: 'superseded', swatch: { kind: 'none' }, text: supersededText(superseded.map(releaseWords)) })
  }
  return entries
}
