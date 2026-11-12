import { own } from '../network/graph'
import { COLOUR_NAMES } from '../network/palette'

// Constants rather than text groups, so that legendEntries stays a pure function of the graph
// (Y24). Each summary sentence says "in view": the summaries are computed over the selected
// releases' results that pass the current filters, never over every result. A threshold is
// "not met" rather than "below": a p-value fails its criterion by being above it.

/** Shown before the family entries when any is present: colour is one channel of several. */
export const LEGEND_HEADING =
  "Colour shows the kind of relationship. Arrowheads and line styles show effect and evidence, and every relationship's panel names its type."

export const LEGEND_TEXT = {
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
} as const

export const SHAPE_WORDS: Readonly<Record<'gene' | 'protein' | 'isoform' | 'other', string>> = {
  gene: 'rectangle rounded at the bottom',
  protein: 'ellipse',
  isoform: 'diamond with rounded corners',
  other: 'triangle with rounded corners',
}

export function circleText(typeLabel: string, roles: { subject: string; object: string } | null): string {
  return roles === null
    ? `Circle at the end of the second role: ${typeLabel}, two fixed roles, from the first role to the second, with no claim of cause or effect.`
    : `Circle at the ${roles.object} end: ${typeLabel}, two fixed roles, from ${roles.subject} to ${roles.object}, with no claim of cause or effect.`
}

/** One sentence per reference type whose links name a release of the graph; a type whose links name none gets none. */
export function referenceText(stated: readonly { typeLabel: string; releases: readonly string[] }[]): string {
  const sentences = stated
    .filter((entry) => entry.releases.length > 0)
    .map((entry) => ` ${entry.typeLabel} links here are stated by ${entry.releases.join(', ')}.`)
  return LEGEND_TEXT.reference + sentences.join('')
}

export function levelText(strings: readonly string[]): string {
  return `Physical interactions are labelled with the strongest level stated by their supporting results in view: ${strings.join('; ')}.`
}

/** A colour without a name is still named as a colour, never as "undefined". */
export function familyText(colour: string, typeLabels: readonly string[]): string {
  return `${own(COLOUR_NAMES, colour) ?? 'Coloured'} line: ${typeLabels.join('; ')}.`
}

export function nodeText(entityTypeLabel: string, shape: 'gene' | 'protein' | 'isoform' | 'other'): string {
  return `${entityTypeLabel}: ${own(SHAPE_WORDS, shape) ?? SHAPE_WORDS.other}.`
}

export function supersededText(releases: readonly string[]): string {
  return `Includes ${releases.join(', ')}, which a newer release has since corrected. It is shown because it was selected, and a mixed effect may be its correction.`
}
