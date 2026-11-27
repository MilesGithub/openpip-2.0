import type { CSSProperties } from 'react'
import { own } from '../network/graph'
import { plural, slugWords } from '../network/words'
import { clipText } from '../panels/text'
import type { GraphDirection, GraphEffectKey, GraphFamily } from '../types'
import { isolateText } from '../words/isolateText'

export const TOGGLES_NAME = 'Network options'
export const FOLLOW_LABEL = 'Follow gene-to-protein links'
export const FOLLOW_NOTE =
  "Adds what reference links join to each chosen entity: a gene's protein entries and their isoforms, an entry's isoforms and genes, and an isoform's entry and that entry's genes."
export const OUTCOME_LABEL = 'Show tested-absent results'
export const OUTCOME_NOTE =
  'Also lists inconclusive and not-significant results: every result that does not support a relationship. Off, the network lists supporting results only.'
export const LAYERS_LEGEND = 'Layers'
export const LAYERS_LOADING = 'The layer boxes appear here once the list of layers arrives.'
export const LAYERS_MINIMUM = 'At least one layer stays on.'
export const LAYER_OFF = 'off'
export const FAMILY_WORDS: Readonly<Record<GraphFamily, string>> = {
  physical: 'Physical interactions',
  regulation: 'Regulation',
  rna: 'RNA regulation',
  genetic: 'Genetic interactions',
  modification: 'Modifications',
  ligand_receptor: 'Ligands and receptors',
  association: 'Associations',
  phenotype: 'Phenotypes',
  membership: 'Memberships',
  reference: 'Reference links',
}

export function familyWords(family: string): string {
  return own(FAMILY_WORDS, family) ?? slugWords(family)
}

export function layerText(name: string, count: number | null, on: boolean): string {
  if (!on) return `${name}: ${LAYER_OFF}`
  if (count === null) return name
  return `${name}: ${plural(count, '1 shown', '{n} shown')}`
}

export function typesGroupName(familyText: string): string {
  return `${familyText}: types`
}

export const FILTERS_NAME = 'Filters'
export const DIRECTION_LABEL = 'Direction from each seed'
export const DIRECTION_WORDS: Readonly<Record<GraphDirection, string>> = { both: 'Both ways', out: 'Outgoing', in: 'Incoming' }
export const DIRECTION_NOTE =
  'Outgoing keeps the relationships whose subject is a seed, and incoming those whose object is one; a symmetric relationship, such as a physical interaction, matches either way.'
export const EFFECT_LEGEND = 'Effect'
export const EFFECT_OPTION_WORDS: Readonly<Record<GraphEffectKey, string>> = {
  activating: 'activates', inhibiting: 'inhibits', unknown: 'effect unknown', not_stated: 'no effect stated',
}
export const EFFECT_NOTE =
  'Applies only to the supporting results of relationship types that state an effect, such as transcriptional regulation; other results stay.'
export const BASIS_LEGEND = 'Evidence basis'
export const METHOD_LABEL = 'Detection method'
export const METHOD_HINT =
  'PSI-MI identifiers, such as MI:0018, separated by commas or spaces; a result matches when it names any one of them.'
export const METHOD_APPLY = 'Apply the detection methods'

export function methodProblemText(piece: string): string {
  return `“${isolateText(clipText(piece))}” is not a PSI-MI identifier: write MI: and four digits, such as MI:0018.`
}

export const SCORE_LEGEND = 'Score types'
export const SCORE_NOTE = 'Keeps the results that carry a score of every checked type.'
export const TAXON_LABEL = 'Organism of the partners'
export const ANY_ORGANISM = 'Any organism'
export const TAXON_NOTE = 'Lists only the partners of this organism; an entity the network starts from is never left out for its own organism.'
export const RELEASES_LEGEND = 'Releases'
export const RELEASES_DEFAULT = 'With no release checked, the view reads every published release you may see.'
export const RELEASES_PINNED = 'Only the checked releases are read.'

export function browserScopeText(slug: string): string {
  return `This view reads only the releases of the browser ${slug}; checking a release here reads that release instead.`
}

export const RELEASES_WAITING = 'The releases appear here once the network is drawn.'
export const RELEASES_UNNAMED = 'This network names no release.'

export function releasesLimitText(max: number): string {
  return plural(max, 'A view reads at most 1 release.', 'A view reads at most {n} releases.')
}

export function pinnedRefText(ref: string): string {
  return `${ref}: no relationship in view lists it`
}

export const CLEAR_FILTERS = 'Clear filters'

export const CONTROL_ROW_STYLE: CSSProperties = { display: 'flex', flexWrap: 'wrap', gap: 16, alignItems: 'flex-start', margin: '12px 0' }
export const GROUP_STYLE: CSSProperties = { minWidth: 0, margin: 0, padding: 0, borderWidth: 0 }
export const LEGEND_STYLE: CSSProperties = { padding: 0, fontWeight: 600 }
export const CHECK_LABEL_STYLE: CSSProperties = { display: 'block', overflowWrap: 'anywhere' }
export const LAYER_TYPES_STYLE: CSSProperties = { marginLeft: 24 }
