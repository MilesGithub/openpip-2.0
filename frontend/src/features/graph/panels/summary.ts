import type {
  GraphEdge, GraphOutcome, GraphPresentation, GraphRelationshipResponse, GraphRelease, GraphReleaseCounts, GraphVocabularies,
} from '../types'
import { GRAPH_OUTCOMES } from '../network/graph'
import type { TypedGraph } from '../network/graph'
import { edgeDescription, typeLabel } from '../network/words'
import {
  INDEPENDENT_LABEL, NOT_LISTED, NOT_REQUESTED, SIGN_MEANING, SUPPORTING_LABEL, contradictionSentences, effectCountsLine,
  effectLine, levelCountsLine, levelLine, neitherLine, outcomeWords, outsideLine, publicationsLine, signCountsLine, signLine,
  testedLevelLines,
} from './panelWords'
import { clipText, sourceText } from './text'

export interface OutcomeCountRow { outcome: GraphOutcome; label: string; results: number; independent: number }
export interface RelationshipSummary {
  typeLabel: string
  definition: string                 // '' when none; through clipText (M36)
  sentence: string                   // edgeDescription(edge, graph, presentation, vocabularies)
  notListed: string | null           // NOT_LISTED when edge.shown is false, else null (M62)
  qualifiers: string[]               // the edge's identity qualifiers, `${clipText(key)}: ${clipText(sourceText(value))}` each (M19); [] for {}
  contradiction: string[]            // contradictionSentences(edge, byRelease, releases, vocabularies); [] when nothing contradicts
  neither: string | null             // neitherLine
  effect: string[]                   // effectLine, effectCountsLine (the non-null ones)
  sign: string[]                     // signLine, signCountsLine, then SIGN_MEANING when signLine is not null
  level: string[]                    // levelLine, levelCountsLine, then testedLevelLines
  headline: [string, string]         // the supporting and independent lines of 8.5
  byOutcome: OutcomeCountRow[]       // four rows, GRAPH_OUTCOMES order
  outside: string                    // outsideLine
  publications: string               // publicationsLine
}

function present(lines: readonly (string | null)[]): string[] {
  return lines.filter((line): line is string => line !== null)
}

/**
 * Every figure is the edge's own: the client never recomputes independence, pools scores, sums
 * distinct counts or reads a summary from a page of rows. The sentence is the network's, so the
 * panel's agrees with the table's and the canvas's.
 */
export function relationshipSummary(edge: GraphEdge, options: {
  graph: TypedGraph                              // graphOfRelationship(page) once the page loads; before, graphWithNodes(kept, selection.nodes) (3)
  requested: readonly GraphOutcome[]             // requestedOf(params)
  byRelease: readonly GraphReleaseCounts[] | null // the page's by_release; null before it loads
  releases: readonly GraphRelease[]              // the panel's merged releases (mergeReleases, 7), for 8.7's correction clause (M26)
  definition: string                             // the page's definition, else the vocabularies' relationship_types entry's, else ''
  presentation?: GraphPresentation | null
  vocabularies?: GraphVocabularies | null
}): RelationshipSummary {
  const { graph, requested, byRelease, releases, definition, presentation, vocabularies } = options
  const sign = signLine(edge)
  const qualifiers: Record<string, unknown> = typeof edge.qualifiers === 'object' && edge.qualifiers !== null ? edge.qualifiers : {}
  return {
    typeLabel: typeLabel(edge.type, vocabularies),
    definition: clipText(definition),
    sentence: edgeDescription(edge, graph, presentation, vocabularies),
    notListed: edge.shown === false ? NOT_LISTED : null,
    qualifiers: Object.keys(qualifiers).map((key) => `${clipText(key)}: ${clipText(sourceText(qualifiers[key]))}`),
    contradiction: contradictionSentences(edge, byRelease, releases, vocabularies),
    neither: neitherLine(edge, requested),
    effect: present([effectLine(edge), effectCountsLine(edge)]),
    sign: present([sign, signCountsLine(edge), sign === null ? null : SIGN_MEANING]),
    level: [...present([levelLine(edge, vocabularies), levelCountsLine(edge, vocabularies)]), ...testedLevelLines(edge, vocabularies)],
    headline: requested.includes('supported')
      ? [`${SUPPORTING_LABEL}: ${edge.assertion_count}`, `${INDEPENDENT_LABEL}: ${edge.evidence_count}`]
      : [`${SUPPORTING_LABEL}: ${NOT_REQUESTED}`, `${INDEPENDENT_LABEL}: ${NOT_REQUESTED}`],
    byOutcome: GRAPH_OUTCOMES.map((outcome) => ({
      outcome,
      label: requested.includes(outcome) ? outcomeWords(outcome) : `${outcomeWords(outcome)} (${NOT_REQUESTED})`,
      results: edge.outcome_counts[outcome],
      independent: edge.independent_outcome_counts[outcome],
    })),
    outside: outsideLine(edge),
    publications: publicationsLine(edge, requested),
  }
}

/**
 * The page's edge once its body passes the guard, else the kept edge, so the panel keeps the kept
 * edge's words while the page loads and after it fails. Null only while neither exists.
 */
export function shownEdgeOf(page: GraphRelationshipResponse | null, kept: GraphEdge | null): GraphEdge | null {
  return page !== null && page !== undefined ? page.edge : kept ?? null
}

/** Nothing reads a null edge's flag. */
export function syntheticFlags(edge: GraphEdge | null): boolean[] {
  return edge === null || edge === undefined ? [] : [edge.is_synthetic]
}
