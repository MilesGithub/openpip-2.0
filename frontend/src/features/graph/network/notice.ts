import type { GraphOutcome } from '../types'
import { GRAPH_OUTCOMES, keptGraph, own } from './graph'
import type { TypedGraph } from './graph'
import { defaultShowNonSupporting, visibleGraph } from './visibility'
import { plural } from './words'

const HIDDEN_WORDS: Readonly<Record<GraphOutcome, string>> = {
  supported: 'supporting',
  tested_absent: 'tested-and-not-detected',
  inconclusive: 'inconclusive',
  not_significant: 'not-significant',
}

function limitWords(codes: ReadonlySet<string>): string {
  const edges = codes.has('edges_truncated')
  const nodes = codes.has('nodes_truncated')
  if (edges && nodes) return ' (the relationship and entity limits)'
  if (edges) return ' (the relationship limit)'
  return nodes ? ' (the entity limit)' : ''
}

/**
 * Over the kept graph, each line only when its count is above zero. Counts are the page's and
 * the canvas's own, never summed across outcomes, and never read from a warning's detail: a
 * warning names a limit by its code alone.
 */
export function noticeLines(graph: TypedGraph | null, options?: { showNonSupporting?: boolean }): string[] {
  if (graph === null || graph === undefined) return []
  const { graph: kept, dropped } = keptGraph(graph)
  const show = options?.showNonSupporting ?? defaultShowNonSupporting(graph)
  // The canvas's own count: an edge it hides is worded by the client-hidden line, never as shown.
  const visible = visibleGraph(kept, { showNonSupporting: show })
  const lines: string[] = []
  const page = kept.page
  if (page !== null) {
    const omittedEdges = page.omitted?.edges ?? 0
    if (omittedEdges > 0) {
      const shown = plural(visible.edges.length, '1 relationship shown', '{n} relationships shown')
      const left = plural(omittedEdges, '1 more that matched was left out', '{n} more that matched were left out')
      const limits = limitWords(new Set(kept.warnings.map((warning) => warning.code)))
      lines.push(`${shown}; ${left} to keep the network within its size limits${limits}.`)
    }
    const candidates = page.omitted?.candidates ?? 0
    if (candidates > 0) {
      lines.push(plural(
        candidates,
        '1 lookup found more relationships than are ranked per seed; only the most covered were considered.',
        '{n} lookups found more relationships than are ranked per seed; only the most covered were considered.',
      ))
    }
    const hidden: string[] = []
    for (const outcome of GRAPH_OUTCOMES) {
      const count = page.hidden?.[outcome] ?? 0
      if (!(count > 0)) continue
      const words = own(HIDDEN_WORDS, outcome) ?? outcome
      hidden.push(plural(
        count,
        `1 relationship with ${words} evidence is hidden by the outcome filter.`,
        `{n} relationships with ${words} evidence are hidden by the outcome filter.`,
      ))
    }
    lines.push(...hidden)
    // The API counts a relationship once under each outcome it has evidence of.
    if (hidden.length >= 2) lines.push('A relationship with evidence of two kinds is counted under each.')
  }
  if (!show) {
    const onCanvas = visible.hiddenEdges.length
    if (onCanvas > 0) {
      lines.push(plural(
        onCanvas,
        '1 relationship listed without supporting evidence is hidden on the canvas.',
        '{n} relationships listed without supporting evidence are hidden on the canvas.',
      ))
    }
  }
  const missing = dropped.filter((element) => element.kind === 'edge' && element.reason === 'missing-endpoint').length
  if (missing > 0) {
    lines.push(plural(
      missing,
      '1 relationship could not be drawn: an entity it names is missing.',
      '{n} relationships could not be drawn: an entity they name is missing.',
    ))
  }
  const refused = dropped.length - missing
  if (refused > 0) {
    lines.push(plural(
      refused,
      '1 element with a repeated or reserved id was left out.',
      '{n} elements with a repeated or reserved id were left out.',
    ))
  }
  return lines
}
