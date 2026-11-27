import { slugWords } from '../network/words'
import type { TypedGraph } from '../network/graph'
import { rowReleaseWords } from '../panels/releases'
import { arrayOf, clipText } from '../panels/text'
import type { GraphEffectKey, GraphVocabularies } from '../types'
import type { ExploreChange, ExploreLimits, ExploreParamName, ExploreView } from '../viewState'
import { isolateText } from '../words/isolateText'
import { taxonWords } from '../words/taxa'
import {
  BASIS_LEGEND, DIRECTION_LABEL, DIRECTION_WORDS, EFFECT_LEGEND, EFFECT_OPTION_WORDS, LAYERS_LEGEND, METHOD_LABEL, OUTCOME_LABEL,
  RELEASES_DEFAULT, RELEASES_LEGEND, RELEASES_PINNED, RELEASES_UNNAMED, RELEASES_WAITING, SCORE_LEGEND, TAXON_LABEL,
  browserScopeText, pinnedRefText, releasesLimitText,
} from './filterWords'

export type ExploreFilterId = 'release' | 'type' | 'direction' | 'effect' | 'outcome' | 'basis' | 'method' | 'score_type' | 'taxon'
export type ExploreFilterControl = 'release_checkboxes' | 'layer_checkboxes' | 'select' | 'checkboxes' | 'toggle' | 'curie_box'
export type ExploreOptionSource =
  | 'answer_and_address' | 'vocabularies_relationship_types' | 'fixed' | 'vocabularies_evidence_bases' | 'typed'
  | 'vocabularies_score_types' | 'vocabularies_taxa'
export interface ExploreFilterEntry {
  id: ExploreFilterId
  names: readonly ExploreParamName[]   // the address names it reads and writes
  wire: readonly string[]              // the network parameters exploreNetworkParams sends for it
  label: string                        // its legend or label, a constant of 7.9
  control: ExploreFilterControl
  source: ExploreOptionSource
  change: ExploreChange                // what its control passes to onViewChange (7.4)
  cleared: boolean                     // whether "Clear filters" resets it
}
/** PLAN:152's nine filters, in its order (Q4). */
export const EXPLORE_FILTER_IDS: readonly ExploreFilterId[] = [
  'release', 'type', 'direction', 'effect', 'outcome', 'basis', 'method', 'score_type', 'taxon',
]
export const EXPLORE_FILTERS: Readonly<Record<ExploreFilterId, ExploreFilterEntry>> = {
  release: {
    id: 'release', names: ['releases', 'browser'], wire: ['releases', 'browser'], label: RELEASES_LEGEND, control: 'release_checkboxes',
    source: 'answer_and_address', change: 'scope', cleared: true,
  },
  type: {
    id: 'type', names: ['layers'], wire: ['layers'], label: LAYERS_LEGEND, control: 'layer_checkboxes', source: 'vocabularies_relationship_types',
    change: 'layers', cleared: false,
  },
  direction: {
    id: 'direction', names: ['direction'], wire: ['direction'], label: DIRECTION_LABEL, control: 'select', source: 'fixed', change: 'direction',
    cleared: true,
  },
  effect: {
    id: 'effect', names: ['effect'], wire: ['effect'], label: EFFECT_LEGEND, control: 'checkboxes', source: 'fixed', change: 'effect', cleared: true,
  },
  outcome: {
    id: 'outcome', names: ['outcome'], wire: ['outcome'], label: OUTCOME_LABEL, control: 'toggle', source: 'fixed', change: 'outcome', cleared: false,
  },
  basis: {
    id: 'basis', names: ['evidence_basis'], wire: ['evidence_basis'], label: BASIS_LEGEND, control: 'checkboxes',
    source: 'vocabularies_evidence_bases', change: 'evidence_basis', cleared: true,
  },
  method: {
    id: 'method', names: ['method'], wire: ['method'], label: METHOD_LABEL, control: 'curie_box', source: 'typed', change: 'method', cleared: true,
  },
  score_type: {
    id: 'score_type', names: ['score_types'], wire: ['score'], label: SCORE_LEGEND, control: 'checkboxes', source: 'vocabularies_score_types',
    change: 'score_types', cleared: true,
  },
  taxon: {
    id: 'taxon', names: ['taxon'], wire: ['taxon'], label: TAXON_LABEL, control: 'select', source: 'vocabularies_taxa', change: 'taxon', cleared: true,
  },
}
/** The Filters group's DOM order (7.8): its first control is always rendered, so "Clear filters" can move focus to it. */
export const FILTER_GROUP_ORDER: readonly ExploreFilterId[] = ['direction', 'effect', 'basis', 'method', 'score_type', 'taxon', 'release']

/** `appended`: the view alone offers it, so its box goes once it is unchecked (7.8). */
export interface CheckOption { value: string; text: string; checked: boolean; off: boolean; appended: boolean }
export interface SelectOption { value: string; text: string }
export const EFFECT_ORDER: readonly GraphEffectKey[] = ['activating', 'inhibiting', 'unknown', 'not_stated']

const holds = (list: readonly string[] | null, value: string) => list !== null && list.indexOf(value) !== -1
const usable = (value: unknown): value is string => typeof value === 'string' && value !== ''

export function directionOptions(): SelectOption[] {
  return (['both', 'out', 'in'] as const).map((value) => ({ value, text: clipText(DIRECTION_WORDS[value]) }))
}

export function effectOptions(view: ExploreView): CheckOption[] {
  return EFFECT_ORDER.map((value) => ({ value, text: clipText(EFFECT_OPTION_WORDS[value]), checked: holds(view.effect, value), off: false, appended: false }))
}

export function basisOptions(view: ExploreView, vocabularies: GraphVocabularies | null | undefined): CheckOption[] {
  const options: CheckOption[] = []
  for (const entry of arrayOf(vocabularies?.evidence_bases)) {
    if (!usable(entry?.value) || options.some((option) => option.value === entry.value)) continue
    const text = usable(entry.label) ? entry.label : slugWords(entry.value)
    options.push({ value: entry.value, text: clipText(text), checked: holds(view.evidence_basis, entry.value), off: false, appended: false })
  }
  for (const value of view.evidence_basis ?? []) {
    if (!options.some((option) => option.value === value)) {
      options.push({ value, text: clipText(slugWords(value)), checked: true, off: false, appended: true })
    }
  }
  return options
}

export function scoreTypeOptions(view: ExploreView, vocabularies: GraphVocabularies | null | undefined): CheckOption[] {
  const options: CheckOption[] = []
  for (const entry of arrayOf(vocabularies?.score_types)) {
    if (!usable(entry?.slug) || options.some((option) => option.value === entry.slug)) continue
    const text = usable(entry.label) ? entry.label : entry.slug
    options.push({ value: entry.slug, text: clipText(text), checked: holds(view.score_types, entry.slug), off: false, appended: false })
  }
  for (const value of view.score_types ?? []) {
    if (!options.some((option) => option.value === value)) options.push({ value, text: clipText(value), checked: true, off: false, appended: true })
  }
  return options
}

export function taxonOptions(view: ExploreView, vocabularies: GraphVocabularies | null | undefined): SelectOption[] {
  const options: SelectOption[] = []
  for (const entry of arrayOf(vocabularies?.taxa)) {
    const taxid: unknown = entry?.taxid
    if (typeof taxid !== 'number' || !Number.isSafeInteger(taxid) || options.some((option) => option.value === String(taxid))) continue
    options.push({ value: String(taxid), text: clipText(taxonWords(taxid, vocabularies)) })
  }
  if (view.taxon !== null && !options.some((option) => option.value === String(view.taxon))) {
    options.push({ value: String(view.taxon), text: clipText(taxonWords(view.taxon, vocabularies)) })
  }
  return options
}

export function releaseOptions(view: ExploreView, graph: TypedGraph | null, limits: ExploreLimits): CheckOption[] {
  const full = view.releases !== null && view.releases.length >= limits.maxReleases
  const options: CheckOption[] = []
  for (const release of graph === null ? [] : graph.releases) {
    if (options.some((option) => option.value === release.id)) continue
    const checked = holds(view.releases, release.id)
    // F3's words around an isolated title, cut so that rowReleaseWords' own cut keeps the version and the flags (7.6).
    const titled = { ...release, dataset: { ...release.dataset, title: isolateText(clipText(release.dataset.title, 200)) } }
    options.push({ value: release.id, text: clipText(rowReleaseWords(titled, release.id)), checked, off: !checked && full, appended: false })
  }
  for (const ref of view.releases ?? []) {
    if (options.some((option) => option.value === ref)) continue
    // Before a graph is held no answer has listed a release or left one out, so the ref stands alone (7.6).
    options.push({ value: ref, text: clipText(graph === null ? ref : pinnedRefText(ref)), checked: true, off: false, appended: true })
  }
  return options
}

export function releaseNotes(view: ExploreView, graph: TypedGraph | null, options: readonly CheckOption[], limits: ExploreLimits): string[] {
  const notes: string[] = []
  notes.push(view.browser !== null ? browserScopeText(view.browser) : view.releases !== null ? RELEASES_PINNED : RELEASES_DEFAULT)
  if (options.length === 0) notes.push(graph === null ? RELEASES_WAITING : RELEASES_UNNAMED)
  if (view.releases !== null && view.releases.length >= limits.maxReleases) notes.push(releasesLimitText(limits.maxReleases))
  return notes
}

export type CheckFilterId = 'effect' | 'basis' | 'score_type' | 'release'

function toggled(list: readonly string[] | null, value: string, on: boolean): string[] | null {
  const kept = (list ?? []).filter((item) => item !== value)
  const next = Array.from(new Set(on ? [...kept, value] : kept)).sort()
  return next.length === 0 ? null : next
}

export function withFilterValue(view: ExploreView, id: CheckFilterId, value: string, on: boolean): ExploreView {
  switch (id) {
    case 'effect':
      if (EFFECT_ORDER.indexOf(value as GraphEffectKey) === -1) return view
      return { ...view, effect: toggled(view.effect, value, on) as GraphEffectKey[] | null }
    case 'basis':
      return { ...view, evidence_basis: toggled(view.evidence_basis, value, on) }
    case 'score_type':
      return { ...view, score_types: toggled(view.score_types, value, on) }
    case 'release':
      return on ? { ...view, releases: toggled(view.releases, value, true), browser: null } : { ...view, releases: toggled(view.releases, value, false) }
    default:
      return view
  }
}

export function withDirection(view: ExploreView, value: string): ExploreView {
  return { ...view, direction: value === 'out' || value === 'in' ? value : null }
}

export function withTaxon(view: ExploreView, value: string): ExploreView {
  if (value === '') return { ...view, taxon: null }
  const number = Number(value)
  return /^[1-9][0-9]*$/.test(value) && Number.isSafeInteger(number) ? { ...view, taxon: number } : view
}

export type MethodsRead = { methods: string[]; problem: null } | { methods: null; problem: string }

export function methodsOfText(text: string): MethodsRead {
  const found: string[] = []
  let piece = ''
  for (const character of Array.from(text)) {
    if (character === ',' || character.trim() === '') {
      if (piece !== '') found.push(piece)
      piece = ''
    } else {
      piece += character
    }
  }
  if (piece !== '') found.push(piece)
  const methods: string[] = []
  for (const item of found) {
    if (!/^[Mm][Ii]:[0-9]{4}$/.test(item)) return { methods: null, problem: item }
    const curie = `MI:${item.slice(3)}`
    if (methods.indexOf(curie) === -1) methods.push(curie)
  }
  return { methods: methods.sort(), problem: null }
}

export function methodsText(methods: readonly string[] | null): string {
  return methods === null ? '' : methods.join(', ')
}

export function isFiltered(view: ExploreView): boolean {
  return view.releases !== null || view.browser !== null || view.direction !== null || view.effect !== null || view.evidence_basis !== null
    || view.method !== null || view.score_types !== null || view.taxon !== null
}

export function withoutFilters(view: ExploreView): ExploreView {
  return {
    ...view, releases: null, browser: null, direction: null, effect: null, evidence_basis: null, method: null, score_types: null, taxon: null,
  }
}
