import type { GraphEntityRef, GraphRelease, GraphVocabularies } from '../types'
import { isolateText } from '../words/isolateText'
import { nodeDerivations } from './graph'
import type { TypedGraph, TypedGraphNode } from './graph'
import { plural, statingReleases, typeLabel } from './words'

// X1's words for derived seeds (Q32). An entity's name and a release's title placed inside fixed words are isolated
// (J31; Q24); a type label is the registry's and is not. A release is named by its title and version, never by a kind,
// which no release entry carries (Q7). Callers cut a source value with clipText before passing it.

/** One path that made a node a derived seed, worded from the graph it was drawn in. */
export interface DerivedPath {
  seed: GraphEntityRef                // the named seed the path starts from
  types: readonly string[]            // each link's type label, in order from the seed
  releases: readonly GraphRelease[]   // the releases that state its links, each once, in the graph's order
}

/** A link the graph does not hold: a hand-built or cut graph, never the API's settled answer (Q15). */
export const UNLISTED_STEP = 'a reference link'
/** The legend's entry, a constant outside LEGEND_TEXT, which a test pins whole (Q31, Q32). */
export const DERIVED_LEGEND =
  'Dashed two-tone outline: an entity reached by following gene-to-protein links from a starting entity (a derived seed). The line under its name gives the type of each link followed.'

export function derivedPaths(node: TypedGraphNode | null | undefined, graph: TypedGraph,
  vocabularies?: GraphVocabularies | null): DerivedPath[] {
  if (node === null || node === undefined || node.is_seed === true) return []
  return nodeDerivations(node).map((derivation) => {
    const types: string[] = []
    const stating = new Set<string>()
    for (const ref of derivation.via) {
      const edge = graph.edges.find((candidate) => candidate.id === ref)
      types.push(edge === undefined ? UNLISTED_STEP : typeLabel(edge.type, vocabularies))
      if (edge !== undefined) for (const release of statingReleases(edge, graph)) stating.add(release.id)
    }
    return { seed: derivation.seed, types, releases: graph.releases.filter((release) => stating.has(release.id)) }
  })
}

export function pathViaText(types: readonly string[]): string {
  return `via ${types.join(', then ')}`
}

export function viaText(paths: readonly DerivedPath[]): string {
  const each: string[] = []
  for (const path of paths) {
    const text = pathViaText(path.types)
    if (!each.includes(text)) each.push(text)
  }
  return each.join(' and ')
}

function oneLine(text: string): string {
  // The label's own line breaks become spaces, as F2's single fillText draws them, so the second line is X1's alone.
  // Found by code point, since lint refuses a control character in a regular expression.
  let line = ''
  for (const character of Array.from(text)) {
    const code = character.codePointAt(0) ?? 0
    line += code === 0x0a || code === 0x0d ? ' ' : character
  }
  return line
}

export function derivedCanvasLabel(label: string, paths: readonly DerivedPath[]): string {
  // A second line, drawn apart from the label, so the label stays F2's own and needs no isolates on the canvas.
  return paths.length === 0 ? label : `${oneLine(label)}\n${viaText(paths)}`
}

export function derivedRowSuffix(paths: readonly DerivedPath[]): string {
  return paths.length === 0 ? '' : `, reached ${viaText(paths)}`
}

export function derivationText(seedName: string, via: string, releaseNames: readonly string[]): string {
  const from = `Reached from ${isolateText(seedName)} ${via}`
  return releaseNames.length === 0 ? `${from}.` : `${from}; stated by ${releaseNames.map((name) => isolateText(name)).join(', ')}.`
}

export function derivedSeedsLeftText(count: number, max: number | null): string {
  // "At least": a step of the closure cut at its row bound counts one, whatever its unread rows reach (Q14).
  const left = plural(count, 'At least 1 entity reached through gene-to-protein links was left out',
    'At least {n} entities reached through gene-to-protein links were left out')
  const cap = typeof max === 'number' && Number.isSafeInteger(max) && max >= 1 ? max : null
  if (cap === null) return `${left} to keep the network within its limits.`
  return `${left}: ${plural(cap, 'a network looks up at most 1 entity, the chosen and the reached together.',
    'a network looks up at most {n} entities, the chosen and the reached together.')}`
}
