import { keptGraph } from '../network/graph'
import type { TypedGraph } from '../network/graph'
import { visibleGraph } from '../network/visibility'
import { typeLabel } from '../network/words'
import { arrayOf, clipText } from '../panels/text'
import type { GraphFamily, GraphVocabularies } from '../types'
import type { ExploreView } from '../viewState'
import { familyWords } from './filterWords'

export type LayerState = 'all' | 'some' | 'off'
export interface LayerTypeOption { slug: string; text: string; on: boolean; count: number | null; held: boolean }
export interface LayerFamilyOption {
  family: string
  text: string
  types: readonly LayerTypeOption[]
  state: LayerState
  count: number | null
  held: boolean
}
export interface LayerExtra { piece: string; text: string }
export interface LayerModel { families: readonly LayerFamilyOption[]; extras: readonly LayerExtra[]; onTypes: number; held: boolean }
/** The non-reference families in G/types.ts's declaration order, the order the legend uses too. */
export const FAMILY_RANK: Readonly<Record<Exclude<GraphFamily, 'reference'>, number>> = {
  physical: 0, regulation: 1, rna: 2, genetic: 3, modification: 4, ligand_receptor: 5, association: 6, phenotype: 7, membership: 8,
}

interface ListedType { slug: string; family: string }

function listedTypes(vocabularies: GraphVocabularies | null | undefined): ListedType[] {
  const types: ListedType[] = []
  for (const entry of arrayOf(vocabularies?.relationship_types)) {
    if (typeof entry?.slug !== 'string' || typeof entry.family !== 'string') continue
    if (entry.is_reference === true || entry.family === 'reference') continue
    if (types.some((type) => type.slug === entry.slug)) continue
    types.push({ slug: entry.slug, family: entry.family })
  }
  return types.sort((a, b) => (a.slug < b.slug ? -1 : a.slug > b.slug ? 1 : 0))
}

function rank(family: string): number {
  return Object.prototype.hasOwnProperty.call(FAMILY_RANK, family) ? FAMILY_RANK[family as keyof typeof FAMILY_RANK] : Number.MAX_SAFE_INTEGER
}

export function layerModel(view: ExploreView, vocabularies: GraphVocabularies | null | undefined, graph: TypedGraph | null, show: boolean): LayerModel {
  const types = listedTypes(vocabularies)
  const layers = view.layers
  const isOn = (type: ListedType) => layers === null || layers.indexOf(type.slug) !== -1 || layers.indexOf(`family:${type.family}`) !== -1
  const visible = graph === null ? null : visibleGraph(keptGraph(graph).graph, { showNonSupporting: show })
  const familyNames = Array.from(new Set(types.map((type) => type.family)))
    .sort((a, b) => rank(a) - rank(b) || (a < b ? -1 : a > b ? 1 : 0))
  const onTypes = types.filter(isOn).length
  const families: LayerFamilyOption[] = familyNames.map((family) => {
    const members = types.filter((type) => type.family === family)
    const typeOptions: LayerTypeOption[] = members.map((type) => {
      const on = isOn(type)
      const count = visible === null || !on ? null : visible.edges.filter((edge) => edge.type === type.slug).length
      return { slug: type.slug, text: clipText(typeLabel(type.slug, vocabularies)), on, count, held: on && onTypes === 1 }
    })
    const onCount = typeOptions.filter((type) => type.on).length
    const state: LayerState = onCount === typeOptions.length ? 'all' : onCount === 0 ? 'off' : 'some'
    const count = visible === null || state === 'off' ? null : visible.edges.filter((edge) => edge.family === family).length
    const held = onTypes > 0 && onCount === onTypes
    return { family, text: familyWords(family), types: typeOptions, state, count, held }
  })
  const extras: LayerExtra[] = []
  for (const piece of layers ?? []) {
    const listed = types.some((type) => type.slug === piece) || familyNames.some((family) => piece === `family:${family}`)
    if (listed || extras.some((extra) => extra.piece === piece)) continue
    extras.push({ piece, text: piece.startsWith('family:') ? familyWords(piece.slice(7)) : clipText(typeLabel(piece, vocabularies)) })
  }
  const held = families.some((family) => family.held || family.types.some((type) => type.held))
  return { families, extras, onTypes, held }
}

function rebuilt(model: LayerModel, onSet: ReadonlySet<string>, extras: readonly LayerExtra[]): readonly string[] | null {
  const listed = model.families.flatMap((family) => family.types.map((type) => type.slug))
  if (listed.every((slug) => onSet.has(slug)) && extras.length === 0) return null
  const next = Array.from(new Set([...Array.from(onSet), ...extras.map((extra) => extra.piece)])).sort()
  return next.length === 0 ? null : next
}

function onSetOf(model: LayerModel): Set<string> {
  return new Set(model.families.flatMap((family) => family.types.filter((type) => type.on).map((type) => type.slug)))
}

export function withLayerFamily(view: ExploreView, model: LayerModel, family: string, on: boolean): ExploreView {
  const onSet = onSetOf(model)
  const found = model.families.find((option) => option.family === family)
  if (found === undefined) return view
  for (const type of found.types) {
    if (on) onSet.add(type.slug)
    else onSet.delete(type.slug)
  }
  if (onSet.size === 0) return view
  return { ...view, layers: rebuilt(model, onSet, model.extras) }
}

export function withLayerType(view: ExploreView, model: LayerModel, slug: string, on: boolean): ExploreView {
  const onSet = onSetOf(model)
  if (on) onSet.add(slug)
  else onSet.delete(slug)
  if (onSet.size === 0) return view
  return { ...view, layers: rebuilt(model, onSet, model.extras) }
}

export function withoutLayerExtra(view: ExploreView, model: LayerModel, piece: string): ExploreView {
  const onSet = onSetOf(model)
  const extras = model.extras.filter((extra) => extra.piece !== piece)
  const next = rebuilt(model, onSet, extras)
  return { ...view, layers: next }
}
