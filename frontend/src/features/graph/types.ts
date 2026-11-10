/**
 * The responses of /api/graph/v1/, one interface per documented shape: the key
 * tables of docs/developer/api/typed-graph.md, as
 * backend/graph/api/serializers.py and backend/browsers/serializers.py declare
 * them. Keys are the wire's.
 *
 * Every key is present on every 200, so `?:` appears only on a map whose keys
 * the API leaves out, and `| null` exactly where a table allows null. The
 * project's tsconfig has strict off, so the compiler cannot see the difference;
 * types.strict.test.ts compiles this file and the fixtures with strict on.
 *
 * Closed sets are string-literal unions; GRAPH_CONTEXT_FACETS alone is also a
 * value, since the wire builder walks it. Registry slugs, warning codes, CURIEs
 * and tags stay `string`: registries and code add values without a new API
 * version. A consumer's switch over a closed set keeps a non-throwing default
 * branch, since a deploy can bring a value early.
 *
 * Every string here is data as a source or curator wrote it: render it as text,
 * never through dangerouslySetInnerHTML (architecture section 9). The URL keys
 * (identifiers[].url, publication.url, licence.url, external_url) are http(s).
 */

// ── Refs: opaque strings of the form kind:number (PAGE, Ids). Never parse one. ──

export type GraphEntityRef = string
export type GraphRelationshipRef = string
export type GraphReleaseRef = string
export type GraphEvidenceRef = string

// ── Closed sets ─────────────────────────────────────────────────────────────

export type GraphFeatureName =
  | 'resolve'
  | 'entities'
  | 'network'
  | 'relationships'
  | 'browsers'
  | 'datasets'
  | 'summary'
  | 'exports'

export type GraphOutcome = 'supported' | 'tested_absent' | 'inconclusive' | 'not_significant'

/** An edge's summary: `not_applicable` for a type without effect polarity. Never `not_stated`. */
export type GraphEdgeEffect = 'activating' | 'inhibiting' | 'mixed' | 'unknown' | 'not_applicable'

/** A stored assertion's effect, a different set from the edge's. */
export type GraphRowEffect = 'activating' | 'inhibiting' | 'unknown'

/** The keys of effect_counts, and the values of the `effect` filter. */
export type GraphEffectKey = 'activating' | 'inhibiting' | 'unknown' | 'not_stated'

export type GraphEdgeSign = 'negative' | 'positive' | 'mixed' | 'not_stated' | 'not_applicable'

export type GraphRowSign = 'negative' | 'positive'

export type GraphSignKey = 'negative' | 'positive' | 'not_stated'

/** Strongest first. */
export type GraphLevel =
  | 'direct_interaction'
  | 'physical_association'
  | 'association'
  | 'not_stated'

export type GraphFamily =
  | 'physical'
  | 'regulation'
  | 'rna'
  | 'genetic'
  | 'modification'
  | 'ligand_receptor'
  | 'association'
  | 'phenotype'
  | 'membership'
  | 'reference'

export type GraphOrientation = 'symmetric' | 'directed' | 'role_ordered'

export type GraphPolarity = 'none' | 'effect' | 'genetic_sign'

export type GraphEvidenceBasis =
  | 'experimental'
  | 'computational_prediction'
  | 'computational_inference'
  | 'statistical'
  | 'text_mining'
  | 'unknown'

export type GraphAssertionMethod = 'manual_assertion' | 'automatic_assertion' | 'unknown'

/** The twelve context.<facet> parameter names, in the API's order. */
export const GRAPH_CONTEXT_FACETS = [
  'host_taxon',
  'tissue',
  'cell_type',
  'cell_line',
  'condition',
  'perturbation',
  'compartment',
  'assay_system',
  'developmental_stage',
  'timepoint',
  'genome_assembly',
  'other',
] as const
export type GraphContextFacet = (typeof GRAPH_CONTEXT_FACETS)[number]

export type GraphIdentifierRole = 'primary' | 'xref' | 'secondary' | 'alias' | 'symbol'

export type GraphEntityStatus = 'active' | 'provisional' | 'merged' | 'retired'

/** Draft and withdrawn releases are never named in a response. */
export type GraphReleaseStatus = 'published' | 'superseded' | 'staged'

export type GraphApprovalScope = 'internal' | 'public_view' | 'public_bulk' | 'external_services'

export type GraphParticipantSide = 'subject' | 'object'

export type GraphResolutionBasis =
  | 'primary'
  | 'xref_unique'
  | 'alias_unique'
  | 'isoform_entry'
  | 'source_local'
  | 'curated'

export type GraphMethodRole = 'detection' | 'participant_identification' | 'evidence_code'

export type GraphPublicationRole = 'reported_in' | 'method_reference' | 'cited'

export type GraphScoreValueKind =
  | 'probability'
  | 'p_value'
  | 'q_value'
  | 'rank'
  | 'count'
  | 'continuous'

export type GraphScoreDirection =
  | 'higher_is_stronger'
  | 'lower_is_stronger'
  | 'signed'
  | 'unordered'

export type GraphTaxonPolicy = 'required' | 'optional' | 'forbidden'

export type GraphIdentityScheme = 'identifier' | 'coordinates' | 'allele' | 'source_local'

export type GraphNamespaceScope = 'global' | 'taxon' | 'assembly'

export type GraphCasePolicy = 'exact' | 'upper' | 'insensitive'

export type GraphVersionPolicy = 'none' | 'split'

export type GraphTermStatus = 'resolved' | 'candidate' | 'ambiguous' | 'unmatched'

/** A candidate's matches come in this order. */
export type GraphMatchKind = 'primary' | 'secondary' | 'xref' | 'symbol' | 'alias' | 'label'

export type GraphDirection = 'out' | 'in' | 'both'

export type GraphInstanceKind = 'typed' | 'portal_ppi' | 'external' | 'metadata_only'

export type GraphInstanceStatus = 'planned' | 'available' | 'retired'

export type GraphInstanceVisibility = 'staff' | 'unlisted' | 'public'

export type GraphSelectionRole = 'primary' | 'context'

export type GraphAvailability = 'live' | 'planned' | 'external' | 'metadata_only' | 'retired'

// ── Errors ──────────────────────────────────────────────────────────────────

/** One entry of a 400's errors. S ProblemSerializer. */
export interface GraphProblem {
  /** A query name, entity_id, relationship_ref or context.<facet>. */
  parameter: string
  /** The value refused; null when no one value is at fault. `""` or null for an empty parameter, depending on the parameter: branch on neither. */
  value: string | null
  /** Possibly `[]`. */
  allowed: string[] | null
  limit: number | null
}

/** A 400's body: `errors` can be `[]` (more than 1,000 query parameters). */
export interface GraphParameterError {
  detail: string
  errors: GraphProblem[]
}

/** The body of a JSON 404, 406, 429 or 503. */
export interface GraphDetail {
  detail: string
}

// ── Status ──────────────────────────────────────────────────────────────────

export interface GraphRegistryVersion {
  code: string
  /** Null while the typed graph is off, and until graph_sync_registry has run. */
  database: string | null
}

export type GraphFeatures = Record<GraphFeatureName, boolean>

export interface GraphLimits {
  q_max_chars: number
  resolve_max_terms: number
  resolve_max_candidates: number
  max_releases: number
  max_seeds: number
  max_layers: number
  max_context_facets: number
  default_max_edges: number
  max_edges: number
  candidates_per_seed: number
  max_nodes: number
  evidence_page_size: number
  evidence_max_page_size: number
  evidence_page_raw_chars: number
  /** The rate of each limit a served route uses, by name: "300/min". */
  throttles: Record<string, string>
}

export interface GraphStatus {
  /** True while TYPED_GRAPH_ENABLED is on. */
  typed_graph: boolean
  api_version: string
  registry_version: GraphRegistryVersion
  /** One boolean per group of routes; every key false while the graph is off. */
  features: GraphFeatures
  /** Present whether the graph is on or off. */
  limits: GraphLimits
}

// ── Vocabularies ────────────────────────────────────────────────────────────

export interface GraphTaxonEntry {
  taxid: number
  scientific_name: string
  common_name: string | null
  rank: string | null
  /** A taxid. */
  parent: number | null
  /** A taxid. */
  merged_into: number | null
}

export interface GraphEntityTypeEntry {
  slug: string
  label: string
  definition: string
  taxon_policy: GraphTaxonPolicy
  identity_scheme: GraphIdentityScheme
  assembly_required: boolean
  display_token: string | null
}

export interface GraphNamespaceEntry {
  slug: string
  label: string
  curie_prefix: string
  /** Sorted slugs. */
  allowed_entity_types: string[]
  scope: GraphNamespaceScope
  /** A taxid. */
  fixed_taxon: number | null
  case_policy: GraphCasePolicy
  version_policy: GraphVersionPolicy
  defines_identity: boolean
  /** A regular expression. */
  pattern: string
  /** `""` means no link. Never build a link from it: use identifiers[].url. */
  url_template: string
  authority: string
}

export interface GraphRelationshipTypeEntry {
  slug: string
  label: string
  definition: string
  family: GraphFamily
  orientation: GraphOrientation
  polarity: GraphPolarity
  /** Sorted slugs. */
  allowed_subject_types: string[]
  /** Sorted slugs. */
  allowed_object_types: string[]
  subject_role_label: string
  object_role_label: string
  self_pairs_allowed: boolean
  psi_mi_anchors: string[]
  display_token: string | null
  is_reference: boolean
}

export interface GraphScoreTypeEntry {
  slug: string
  label: string
  definition: string
  value_kind: GraphScoreValueKind
  direction: GraphScoreDirection
  range_min: number | null
  range_max: number | null
  unit: string | null
  null_model: string | null
  defining_document: string
  comparable_across_datasets: boolean
}

export interface GraphClosedEntry<TValue extends string = string> {
  value: TValue
  /** The value in words. */
  label: string
}

export interface GraphSummaryEntry<TValue extends string = string> extends GraphClosedEntry<TValue> {
  /** False for a value only a summary gives, which the database never holds. */
  stored: boolean
}

export interface GraphLevelEntry extends GraphClosedEntry<GraphLevel> {
  /** 3 to 0, strongest first. */
  strength: number
}

export interface GraphVocabularies {
  api_version: string
  registry_version: GraphRegistryVersion
  /** Sorted by taxid. */
  taxa: GraphTaxonEntry[]
  /** Sorted by slug, as the next three are. */
  entity_types: GraphEntityTypeEntry[]
  namespaces: GraphNamespaceEntry[]
  relationship_types: GraphRelationshipTypeEntry[]
  score_types: GraphScoreTypeEntry[]
  /** Closed sets keep their declared order. */
  outcomes: GraphClosedEntry<GraphOutcome>[]
  effects: GraphSummaryEntry<GraphEffectKey | 'mixed' | 'not_applicable'>[]
  genetic_signs: GraphSummaryEntry<GraphSignKey | 'mixed' | 'not_applicable'>[]
  evidence_bases: GraphClosedEntry<GraphEvidenceBasis>[]
  assertion_methods: GraphClosedEntry<GraphAssertionMethod>[]
  context_facets: GraphClosedEntry<GraphContextFacet>[]
  interaction_levels: GraphLevelEntry[]
  identifier_roles: GraphClosedEntry<GraphIdentifierRole>[]
}

// ── Shared by the routes that read data ─────────────────────────────────────

export interface GraphPrimaryIdentifier {
  namespace: string
  value: string
}

/** Every entity in a response. PAGE, Entity nodes. */
export interface GraphNode {
  id: GraphEntityRef
  /** An entity type's slug. */
  type: string
  /** Never null: the identifier or the ref when no readable label exists. */
  label: string
  taxon: number | null
  /** Every node has a value today, but a client must accept null (L2's nodes have none). */
  status: GraphEntityStatus | null
  primary_identifier: GraphPrimaryIdentifier | null
}

export interface GraphReleaseDataset {
  slug: string
  title: string
}

/** Always an object; each value is a string or null. */
export interface GraphLicence {
  name: string | null
  spdx_id: string | null
  url: string | null
}

/** One release a response names. PAGE, Release entries. */
export interface GraphRelease {
  id: GraphReleaseRef
  dataset: GraphReleaseDataset
  version: string
  status: GraphReleaseStatus
  superseded: boolean
  is_synthetic: boolean
  licence: GraphLicence
  /** Possibly `""`. */
  attribution: string
  /** Null when the release names no adapter this portal has. */
  changes_note: string | null
  /** Every effective scope, in the API's order. Every caller sees them. */
  scopes: GraphApprovalScope[]
}

export interface GraphWarning {
  /** Open: resolve's `candidates_truncated`, network's `seed_truncated`, `partner_edges_truncated`, `edges_truncated`, `nodes_truncated`, `<outcome>_hidden`. */
  code: string
  detail: string
}

export interface GraphDistinct {
  publications: number
  releases: number
  datasets: number
}

/** One relationship: `network.edges[]`, the relationship page's `edge`, the entity page's `reference_relationships[]`. PAGE, Edges. */
export interface GraphEdge {
  id: GraphRelationshipRef
  /** A relationship type's slug. */
  type: string
  family: GraphFamily
  orientation: GraphOrientation
  /** The stored subject; a symmetric pair is stored lower id first. */
  source: GraphEntityRef
  target: GraphEntityRef
  /** Refs of the releases whose covered evidence the response lists; `[]` is possible. */
  releases: GraphReleaseRef[]
  /** False when `releases` is empty, even on a synthetic release: a badge also reads the response's releases. */
  is_synthetic: boolean
  /** `{}` until identity qualifiers are recorded. */
  qualifiers: Record<string, unknown>
  /** False only on the page of a relationship with none of the requested outcomes. */
  shown: boolean
  /** `not_applicable` for a type without effect polarity whatever its evidence; null when a polarity type has no covered support. */
  effect: GraphEdgeEffect | null
  effect_counts: Record<GraphEffectKey, number>
  /** `not_applicable` for a type without sign polarity whatever its evidence; null when a polarity type has no covered support. */
  genetic_sign: GraphEdgeSign | null
  sign_counts: Record<GraphSignKey, number>
  /** Over the assertions that pass every filter but the outcome filter. */
  outcome_counts: Record<GraphOutcome, number>
  independent_outcome_counts: Record<GraphOutcome, number>
  assertion_count: number
  evidence_count: number
  distinct: GraphDistinct
  /** Only the bases that have supporting evidence. */
  by_basis: Partial<Record<GraphEvidenceBasis, number>>
  /** Physical family only; null for other families and without support. */
  level: GraphLevel | null
  /** All four keys for the physical family, `{}` otherwise. */
  level_counts: Partial<Record<GraphLevel, number>>
  /** Null outside the physical family or without support. */
  level_assigned: boolean | null
  /** Levels strongest first; only the non-supporting outcomes that state one; `{}` otherwise. */
  tested_levels: Partial<Record<GraphOutcome, GraphLevel[]>>
  outside_filters: number
  outside_outcome_counts: Record<GraphOutcome, number>
}

/** The selection as every read route echoes it. */
export interface GraphSelectionEcho {
  /** The distinct refs named, sorted by id; never reduced to what the caller may see. */
  releases: GraphReleaseRef[] | null
  browser: string | null
}

// ── Resolve ─────────────────────────────────────────────────────────────────

export interface GraphMatch {
  kind: GraphMatchKind
  /** Null for a label. */
  namespace: string | null
  value: string
  case_exact: boolean
  version_mismatch: boolean
}

export interface GraphCandidate {
  entity: GraphNode
  exact_primary: boolean
  matches: GraphMatch[]
  releases: GraphReleaseRef[]
}

export interface GraphResolveResult {
  term: string
  status: GraphTermStatus
  /** More than 20 entities match, and 20 are listed. */
  truncated: boolean
  /** At most 20; `[]` when the term is unmatched. */
  candidates: GraphCandidate[]
}

export interface GraphResolveQuery extends GraphSelectionEcho {
  /** The terms as read, trimmed, duplicates kept. */
  q: string[]
  namespace: string | null
  taxon: number | null
  entity_type: string | null
}

export interface GraphResolvePage {
  truncated: boolean
  max_candidates: number
  /** `{}`, or `{terms: n}` when n distinct terms were cut. */
  omitted: { terms?: number }
}

export interface GraphResolveResponse {
  api_version: string
  query: GraphResolveQuery
  /** One entry per term of query.q, in that order. */
  results: GraphResolveResult[]
  /** `[]` when every term is unmatched. */
  releases: GraphRelease[]
  page: GraphResolvePage
  warnings: GraphWarning[]
}

// ── Entities ────────────────────────────────────────────────────────────────

export interface GraphIdentifier {
  namespace: string
  value: string
  version: string | null
  role: GraphIdentifierRole
  /** An http(s) address, or null. */
  url: string | null
}

export interface GraphEntityQuery extends GraphSelectionEcho {
  entity_id: GraphEntityRef
}

export interface GraphEntityResponse {
  api_version: string
  query: GraphEntityQuery
  entity: GraphNode
  /** `""` when the caller may not read it. Never null. */
  description: string
  /** The creator's ref only when `releases` lists it. */
  created_by_release: GraphReleaseRef | null
  identifiers: GraphIdentifier[]
  reference_relationships: GraphEdge[]
  nodes: GraphNode[]
  /** Relationships per type slug, sorted; `{}` when none. */
  relationship_counts: Record<string, number>
  releases: GraphRelease[]
}

// ── The evidence-level filters, as the three routes that take them echo them ──

export interface GraphFiltersEcho {
  outcome: (GraphOutcome | 'all')[] | null
  evidence_basis: GraphEvidenceBasis[] | null
  effect: GraphEffectKey[] | null
  /** Normalized PSI-MI CURIEs. */
  method: string[] | null
  level: GraphLevel[] | null
  /** The values as given, in order. */
  score: string[] | null
  /** {facet: value} of the facets given; `{}` when none. */
  context: Partial<Record<GraphContextFacet, string>>
}

// ── Network ─────────────────────────────────────────────────────────────────

/** A node of a network response, with one key added. Node keys are additive (X1 adds more). */
export interface GraphNetworkNode extends GraphNode {
  /** True for a seed the caller named and may see. */
  is_seed: boolean
}

export interface GraphNetworkQuery extends GraphFiltersEcho, GraphSelectionEcho {
  /** The distinct refs named, sorted by id. */
  seeds: GraphEntityRef[]
  /** Distinct, in order of first appearance. */
  layers: string[] | null
  direction: GraphDirection | null
  taxon: number | null
  include_partner_edges: boolean | null
  max_edges: number | null
}

export interface GraphNetworkPage {
  truncated: boolean
  /** The cap in force. */
  max_edges: number
  /** `{}`, or `candidates` and/or `edges`, each absent when zero. */
  omitted: { candidates?: number; edges?: number }
  /** Always the four outcome keys. */
  hidden: Record<GraphOutcome, number>
}

export interface GraphNetworkResponse {
  api_version: string
  query: GraphNetworkQuery
  /** Sorted by numeric id; `[]` when no seed is visible. */
  nodes: GraphNetworkNode[]
  /** In rank order, every one with `shown` true. */
  edges: GraphEdge[]
  releases: GraphRelease[]
  page: GraphNetworkPage
  warnings: GraphWarning[]
}

// ── Relationships ───────────────────────────────────────────────────────────

export interface GraphRelationshipQuery extends GraphFiltersEcho, GraphSelectionEcho {
  relationship_ref: GraphRelationshipRef
}

export interface GraphReleaseCounts {
  release: GraphReleaseRef
  outcome_counts: Record<GraphOutcome, number>
}

export interface GraphRelationshipResponse {
  api_version: string
  query: GraphRelationshipQuery
  edge: GraphEdge
  /** The type's definition; possibly `""`. */
  definition: string
  /** Each end, sorted by numeric id; one node for a self-pair. */
  nodes: GraphNode[]
  by_release: GraphReleaseCounts[]
  releases: GraphRelease[]
}

// ── Evidence ────────────────────────────────────────────────────────────────

export interface GraphEvidenceQuery extends GraphRelationshipQuery {
  after: GraphEvidenceRef | null
  page_size: number | null
}

export interface GraphTermRef {
  curie: string
  /** Null when the caller may not read the release that labelled it. */
  label: string | null
}

export interface GraphSourceRecord {
  key: string
  line_number: number
  /** The file's columns as the adapter stored them, for staff or a public_view release; null otherwise and when the release stores no raw rows. */
  raw: Record<string, unknown> | null
}

export interface GraphEvidenceMethod {
  role: GraphMethodRole
  curie: string
  label: string | null
}

export interface GraphEvidenceScore {
  /** A score type's slug, holding a colon. */
  type: string
  label: string
  /** Null when the raw value gives no finite number. */
  value: number | null
  raw_value: string
  value_kind: GraphScoreValueKind
  direction: GraphScoreDirection
  unit: string | null
}

export interface GraphEvidenceContext {
  facet: GraphContextFacet
  term: GraphTermRef | null
  /** A taxid, for host_taxon. */
  taxon: number | null
  /** Possibly `""`. */
  label: string
  raw_value: string
}

export interface GraphEvidenceParticipant {
  side: GraphParticipantSide
  entity: GraphEntityRef
  /** The isoform the source named, when the caller may see it. */
  stated_entity: GraphEntityRef | null
  identifier_as_stated: string
  resolution_basis: GraphResolutionBasis
}

export interface GraphPublication {
  pmid: string | null
  doi: string | null
  pmcid: string | null
  imex_id: string | null
  local_key: string | null
  /** Possibly `""`, as are authors and journal. */
  title: string
  authors: string
  journal: string
  year: number | null
  url: string | null
  is_synthetic: boolean
}

export interface GraphEvidencePublication {
  role: GraphPublicationRole
  publication: GraphPublication
}

export interface GraphEvidenceRow {
  id: GraphEvidenceRef
  release: GraphReleaseRef
  /** Null for a row with no stored record, which no row has yet (L2 adds them). */
  record: GraphSourceRecord | null
  outcome: GraphOutcome
  evidence_basis: GraphEvidenceBasis
  assertion_method: GraphAssertionMethod
  eco_term: GraphTermRef | null
  /** Null when the row states none. */
  effect: GraphRowEffect | null
  /** Possibly `""`. */
  effect_as_stated: string
  genetic_sign: GraphRowSign | null
  interaction_type: GraphTermRef | null
  /** Null outside the physical family. */
  interaction_level: GraphLevel | null
  /** Possibly `""`. */
  relationship_as_stated: string
  methods: GraphEvidenceMethod[]
  scores: GraphEvidenceScore[]
  contexts: GraphEvidenceContext[]
  /** Two, the subject first. */
  participants: GraphEvidenceParticipant[]
  /** `{}`, the failed `threshold`, or any qualifier:<key> column: an open object. */
  qualifiers: Record<string, unknown>
  /** Possibly `""`. */
  evidence_group: string
  publications: GraphEvidencePublication[]
}

export interface GraphEvidencePage {
  /** The row cap in force. */
  page_size: number
  /** The ref of the page's last row when more follow, or null. A short page, even an empty one, is not the last page. */
  next: GraphEvidenceRef | null
}

export interface GraphEvidenceResponse {
  api_version: string
  query: GraphEvidenceQuery
  evidence: GraphEvidenceRow[]
  /** Only the releases this page's rows name. */
  releases: GraphRelease[]
  page: GraphEvidencePage
}

// ── Browsers ────────────────────────────────────────────────────────────────

/** Schema v1, as stored: `{}` means every default, and each key may be absent. Its names stay open: the lists are provisional and F2 and F3 pin them. */
export interface GraphPresentation {
  version?: number
  default_layers?: string[]
  /** `direction` a string, the others lists of names. */
  default_filters?: Record<string, string | string[]>
  filter_panels?: string[]
  layout?: string
  evidence_panels?: string[]
  /** A namespace's slug, or null for the entity's own label. */
  node_label_namespace?: string | null
  accent?: string
}

export interface GraphInstanceSelection {
  release: GraphReleaseRef
  role: GraphSelectionRole
}

/** An instance as the gallery lists it; its page shows these keys first. */
export interface GraphInstanceItem {
  slug: string
  title: string
  /** Possibly `""`, as summary is. */
  short_title: string
  summary: string
  kind: GraphInstanceKind
  /** Stored. */
  status: GraphInstanceStatus
  visibility: GraphInstanceVisibility
  /** Computed for the caller on every request. */
  availability: GraphAvailability
  is_synthetic: boolean
  /** An external instance's https address; null for every other kind. */
  external_url: string | null
  /** Open: the tag list grows with later milestones. */
  tags: string[]
  /** Ascending taxids. */
  taxa: number[]
  primary_taxon: number | null
  /** Ascending slugs. */
  relationship_types: string[]
  assays: GraphTermRef[]
  publications: GraphPublication[]
}

export interface GraphInstance extends GraphInstanceItem {
  /** Possibly `""`, as methods_text is. */
  description: string
  methods_text: string
  /** Ascending slugs, never derived. */
  stated_entity_types: string[]
  /** The selected releases the caller may see. */
  selection: GraphInstanceSelection[]
  presentation: GraphPresentation
  allow_external_services: boolean
  external_services_effective: boolean
}

export interface GraphBrowsersQuery {
  q: string | null
  tag: string | null
  relationship_type: string | null
  taxon: number | null
  assay: string | null
  year: number | null
  status: GraphInstanceStatus | null
  availability: GraphAvailability | null
}

export interface GraphBrowsersPage {
  /** Null in this version, and the route takes no cursor: useGraphBrowsers reads one page. */
  next: string | null
}

export interface GraphBrowsersResponse {
  api_version: string
  query: GraphBrowsersQuery
  instances: GraphInstanceItem[]
  page: GraphBrowsersPage
}

export interface GraphBrowserQuery {
  slug: string
}

export interface GraphBrowserResponse {
  api_version: string
  query: GraphBrowserQuery
  instance: GraphInstance
  /** The selected releases the caller may see, sorted by id; `[]` when none. */
  releases: GraphRelease[]
}
