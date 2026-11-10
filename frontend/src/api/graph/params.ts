import { GRAPH_CONTEXT_FACETS } from '../../features/graph/types'
import type {
  GraphAvailability,
  GraphContextFacet,
  GraphDirection,
  GraphEffectKey,
  GraphEntityRef,
  GraphEvidenceBasis,
  GraphEvidenceRef,
  GraphInstanceStatus,
  GraphLevel,
  GraphOutcome,
  GraphReleaseRef,
} from '../../features/graph/types'

export type GraphWireValue = string | number | boolean | string[] | undefined
export type GraphWireParams = Record<string, GraphWireValue>

/** Which releases a route reads. `browser` and `releases` together are a 400, so the type keeps them apart. */
export type GraphSelection =
  | { releases?: GraphReleaseRef[]; browser?: never }
  | { browser?: string; releases?: never }

/** The evidence-level filters of network, relationships and evidence. */
export interface GraphEvidenceFilters {
  /** `'all'` stands alone. */
  outcome?: GraphOutcome[] | 'all'
  evidence_basis?: GraphEvidenceBasis[]
  effect?: GraphEffectKey[]
  /** PSI-MI CURIEs. */
  method?: string[]
  level?: GraphLevel[]
  /** `slug:min:max`, always two colons; a string, since String(1e-7) is refused. */
  score?: string[]
  /** At most four facets; the server refuses a fifth. */
  context?: Partial<Record<GraphContextFacet, string>>
}

export type GraphResolveParams = GraphSelection & {
  /** Sent comma-joined. A comma, tab or new line inside a term is split into more terms by the server, and cannot be escaped (PAGE:498-501). */
  q: string[]
  namespace?: string
  taxon?: number
  entity_type?: string
}

export type GraphNetworkParams = GraphSelection &
  GraphEvidenceFilters & {
    seeds: GraphEntityRef[]
    layers?: string[]
    direction?: GraphDirection
    taxon?: number
    include_partner_edges?: boolean
    max_edges?: number
  }

export type GraphRelationshipParams = GraphSelection & GraphEvidenceFilters

export type GraphEvidenceParams = GraphRelationshipParams & { page_size?: number }

export interface GraphBrowsersParams {
  /** One phrase, never split. */
  q?: string
  tag?: string
  relationship_type?: string
  taxon?: number
  assay?: string
  year?: number
  status?: GraphInstanceStatus
  availability?: GraphAvailability
}

/**
 * Distinct, in plain string order, the empties gone. `[]` stays `[]`: it
 * disables the hook. `null` is absent, like `undefined`, as the list or as an
 * element: strict is off, and URLSearchParams.get returns it. Never numeric,
 * never lowercased.
 */
function cleanList<T extends string>(values: readonly T[] | null | undefined): T[] | undefined {
  if (values === undefined || values === null) return undefined
  return Array.from(new Set(values.filter((value) => value !== '' && value !== null && value !== undefined))).sort()
}

function cleanText<T extends string>(value: T | null | undefined): T | undefined {
  return value === '' || value === null ? undefined : value
}

function cleanPhrase(value: string | null | undefined): string | undefined {
  return cleanText(value?.trim())
}

/** NaN is a blank number box (parseInt('')), which axios would send as `name=NaN`, always a 400. */
function cleanNumber(value: number | null | undefined): number | undefined {
  return typeof value === 'number' && Number.isFinite(value) ? value : undefined
}

function normalizeContext(context: GraphEvidenceFilters['context'] | null): GraphEvidenceFilters['context'] {
  if (context === undefined || context === null) return undefined
  const clean: Partial<Record<GraphContextFacet, string>> = {}
  for (const facet of GRAPH_CONTEXT_FACETS) {
    const value = context[facet]
    if (value !== undefined && value !== null && value !== '') clean[facet] = value
  }
  return Object.keys(clean).length > 0 ? clean : undefined
}

function normalizeFilters(filters: GraphEvidenceFilters): GraphEvidenceFilters {
  return {
    outcome: filters.outcome === 'all' ? 'all' : cleanList(filters.outcome),
    evidence_basis: cleanList(filters.evidence_basis),
    effect: cleanList(filters.effect),
    method: cleanList(filters.method),
    level: cleanList(filters.level),
    score: cleanList(filters.score),
    context: normalizeContext(filters.context),
  }
}

export function normalizeSelection(selection: GraphSelection): GraphSelection {
  const browser = cleanText(selection.browser)
  return browser !== undefined ? { browser } : { releases: cleanList(selection.releases) }
}

/** `q` keeps its order and its duplicates, since results are positional. */
export function normalizeResolveParams(params: GraphResolveParams): GraphResolveParams {
  return {
    q: (params.q ?? []).map((term) => term?.trim()).filter((term) => term !== '' && term !== undefined),
    namespace: cleanText(params.namespace),
    taxon: cleanNumber(params.taxon),
    entity_type: cleanText(params.entity_type),
    ...normalizeSelection(params),
  }
}

export function normalizeNetworkParams(params: GraphNetworkParams): GraphNetworkParams {
  return {
    seeds: cleanList(params.seeds) ?? [],
    layers: cleanList(params.layers),
    direction: cleanText(params.direction),
    ...normalizeFilters(params),
    taxon: cleanNumber(params.taxon),
    include_partner_edges: params.include_partner_edges ?? undefined,
    max_edges: cleanNumber(params.max_edges),
    ...normalizeSelection(params),
  }
}

export function normalizeRelationshipParams(params: GraphRelationshipParams): GraphRelationshipParams {
  return { ...normalizeFilters(params), ...normalizeSelection(params) }
}

export function normalizeEvidenceParams(params: GraphEvidenceParams): GraphEvidenceParams {
  return { ...normalizeRelationshipParams(params), page_size: cleanNumber(params.page_size) }
}

export function normalizeBrowsersParams(params: GraphBrowsersParams): GraphBrowsersParams {
  return {
    q: cleanPhrase(params.q),
    tag: cleanText(params.tag),
    relationship_type: cleanText(params.relationship_type),
    taxon: cleanNumber(params.taxon),
    assay: cleanText(params.assay),
    year: cleanNumber(params.year),
    status: cleanText(params.status),
    availability: cleanText(params.availability),
  }
}

/** An empty list is a 400 and absent means a default, so `[]` can be neither sent nor dropped: the hook is disabled. */
export function hasEmptyList(normalized: object): boolean {
  return Object.values(normalized).some((value) => Array.isArray(value) && value.length === 0)
}

/** False for what cannot be one path segment: an absent or empty id matches no route, and a URL parser removes `.` and `..`, percent-encoded or not, so the request would reach another path. */
export function isGraphPathSegment(value: string | null | undefined): boolean {
  return Boolean(value) && value !== '.' && value !== '..'
}

function joined(list: readonly string[] | undefined): string | undefined {
  if (list === undefined) return undefined
  if (list.length === 0) throw new Error('An empty list is no request: it would read as the default.')
  return list.join(',')
}

function repeated(list: readonly string[] | undefined): string[] | undefined {
  if (list !== undefined && list.length === 0) {
    throw new Error('An empty list is no request: it would read as the default.')
  }
  return list === undefined ? undefined : [...list]
}

function filtersWire(filters: GraphEvidenceFilters): GraphWireParams {
  const wire: GraphWireParams = {
    outcome: filters.outcome === 'all' ? 'all' : joined(filters.outcome),
    evidence_basis: joined(filters.evidence_basis),
    effect: joined(filters.effect),
    method: joined(filters.method),
    level: joined(filters.level),
    score: repeated(filters.score),
  }
  for (const facet of GRAPH_CONTEXT_FACETS) {
    const value = filters.context?.[facet]
    if (value !== undefined) wire[`context.${facet}`] = value
  }
  return wire
}

export function selectionWire(selection: GraphSelection): GraphWireParams {
  return { releases: joined(selection.releases), browser: selection.browser }
}

export function resolveWire(params: GraphResolveParams): GraphWireParams {
  return {
    q: joined(params.q),
    namespace: params.namespace,
    taxon: params.taxon,
    entity_type: params.entity_type,
    ...selectionWire(params),
  }
}

export function networkWire(params: GraphNetworkParams): GraphWireParams {
  return {
    seeds: joined(params.seeds),
    layers: joined(params.layers),
    direction: params.direction,
    ...filtersWire(params),
    taxon: params.taxon,
    include_partner_edges: params.include_partner_edges,
    max_edges: params.max_edges,
    ...selectionWire(params),
  }
}

export function relationshipWire(params: GraphRelationshipParams): GraphWireParams {
  return { ...filtersWire(params), ...selectionWire(params) }
}

/** `after` is the infinite query's page param: it is never in the key, and absent on the first page. */
export function evidenceWire(params: GraphEvidenceParams, after: GraphEvidenceRef | null): GraphWireParams {
  return {
    ...filtersWire(params),
    after: after ?? undefined,
    page_size: params.page_size,
    ...selectionWire(params),
  }
}

export function browsersWire(params: GraphBrowsersParams): GraphWireParams {
  return { ...params }
}
