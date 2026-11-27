import type {
  GraphAvailability,
  GraphBrowserResponse,
  GraphBrowsersResponse,
  GraphCandidate,
  GraphEdge,
  GraphEdgeEffect,
  GraphEntityResponse,
  GraphEntityStatus,
  GraphEvidencePublication,
  GraphEvidenceResponse,
  GraphEvidenceRow,
  GraphFamily,
  GraphInstance,
  GraphInstanceItem,
  GraphInstanceKind,
  GraphInstanceStatus,
  GraphInstanceVisibility,
  GraphLevel,
  GraphMatch,
  GraphNetworkNode,
  GraphNetworkResponse,
  GraphNode,
  GraphNodeDerivation,
  GraphOrientation,
  GraphPresentation,
  GraphPublication,
  GraphRelationshipResponse,
  GraphRelationshipTypeEntry,
  GraphRelease,
  GraphResolveResponse,
  GraphResolveResult,
  GraphScoreTypeEntry,
  GraphSelectionRole,
} from '../../../features/graph/types'

/**
 * Hand-written shapes the synthetic demonstration cannot produce. Unlike the
 * recorded files beside it, nothing ties these to the API: they are for a
 * test that needs a case the demonstration does not have.
 */

export function makeGraphNode(overrides: Partial<GraphNode> = {}): GraphNode {
  return {
    id: 'entity:7',
    type: 'gene',
    label: 'SYN-TF1',
    taxon: 32630,
    status: 'provisional',
    primary_identifier: { namespace: 'synthetic_gene', value: 'SYN-TF1' },
    ...overrides,
  }
}

export function makeGraphEvidenceRow(overrides: Partial<GraphEvidenceRow> = {}): GraphEvidenceRow {
  return {
    id: 'evidence:21',
    release: 'release:2',
    record: { key: 'syn-03', line_number: 4, raw: null },
    outcome: 'supported',
    evidence_basis: 'experimental',
    assertion_method: 'manual_assertion',
    eco_term: null,
    effect: 'activating',
    effect_as_stated: '',
    genetic_sign: null,
    interaction_type: null,
    interaction_level: null,
    relationship_as_stated: '',
    methods: [],
    scores: [],
    contexts: [],
    participants: [
      { side: 'subject', entity: 'entity:8', stated_entity: null, identifier_as_stated: 'SYN-TF2', resolution_basis: 'primary' },
      { side: 'object', entity: 'entity:2', stated_entity: null, identifier_as_stated: 'SYN-G2', resolution_basis: 'primary' },
    ],
    qualifiers: {},
    evidence_group: '',
    publications: [],
    ...overrides,
  }
}

export const nodeWithoutStatus = makeGraphNode({ id: 'entity:900', label: 'SYN-EXTRA', status: null, taxon: null, primary_identifier: null })
export const rowWithoutRecord = makeGraphEvidenceRow({ id: 'evidence:900', record: null })
export const rowWithProvenance = makeGraphEvidenceRow({
  id: 'evidence:901',
  eco_term: { curie: 'ECO:0000000', label: null },
  effect_as_stated: 'activates',
  relationship_as_stated: 'regulates',
  methods: [{ role: 'detection', curie: 'MI:0018', label: 'two hybrid' }],
  contexts: [{ facet: 'tissue', term: null, taxon: null, label: 'liver', raw_value: 'liver' }],
  scores: [{ type: 'synthetic:confidence', label: 'Synthetic confidence', value: null, raw_value: '<2.2e-16', value_kind: 'probability', direction: 'higher_is_stronger', unit: null }],
})
/** Every key of presentation schema v1, each value one the API's validator accepts. */
export const presentationAllKeys: GraphPresentation = {
  version: 1,
  default_layers: ['transcriptional_regulation'],
  default_filters: { direction: 'both', outcome: ['supported'] },
  filter_panels: ['layers', 'outcome'],
  layout: 'cose',
  evidence_panels: ['summary', 'evidence'],
  node_label_namespace: 'synthetic_gene',
  accent: 'teal',
}
/** A page that holds no rows but says more follow: the walk goes on. */
export const evidencePageNoRowsWithNext: GraphEvidenceResponse = {
  api_version: 'graph/v1',
  query: {
    relationship_ref: 'relationship:9',
    outcome: null, evidence_basis: null, effect: null, method: null, level: null, score: null, context: {},
    after: 'evidence:21', page_size: null, releases: null, browser: null,
  },
  evidence: [],
  releases: [],
  page: { page_size: 50, next: 'evidence:22' },
}

// Networks. The makers give a complete, valid answer whose refs start at 900; each named shape
// after them is a case the demonstration cannot produce, so no recording can hold it.

function extrasRelease(): GraphRelease {
  return {
    id: 'release:901',
    dataset: { slug: 'synthetic-extras', title: 'Synthetic extras' },
    version: 'x1',
    status: 'published',
    superseded: false,
    is_synthetic: true,
    licence: { name: null, spdx_id: null, url: null },
    attribution: '',
    changes_note: null,
    scopes: ['internal'],
  }
}

/** A supported, directed, activating regulation edge from entity:901 to entity:902, with one publication. */
export function makeGraphEdge(overrides: Partial<GraphEdge> = {}): GraphEdge {
  return {
    id: 'relationship:901',
    type: 'transcriptional_regulation',
    family: 'regulation',
    orientation: 'directed',
    source: 'entity:901',
    target: 'entity:902',
    releases: ['release:901'],
    is_synthetic: true,
    qualifiers: {},
    shown: true,
    effect: 'activating',
    effect_counts: { activating: 1, inhibiting: 0, unknown: 0, not_stated: 0 },
    genetic_sign: 'not_applicable',
    sign_counts: { negative: 0, positive: 0, not_stated: 1 },
    outcome_counts: { supported: 1, tested_absent: 0, inconclusive: 0, not_significant: 0 },
    independent_outcome_counts: { supported: 1, tested_absent: 0, inconclusive: 0, not_significant: 0 },
    assertion_count: 1,
    evidence_count: 1,
    distinct: { publications: 1, releases: 1, datasets: 1 },
    by_basis: { experimental: 1 },
    level: null,
    level_counts: {},
    level_assigned: null,
    tested_levels: {},
    outside_filters: 0,
    outside_outcome_counts: { supported: 0, tested_absent: 0, inconclusive: 0, not_significant: 0 },
    ...overrides,
  }
}

export function makeNetworkNode(overrides: Partial<GraphNetworkNode> = {}): GraphNetworkNode {
  return {
    id: 'entity:901',
    type: 'gene',
    label: 'SYN-N901',
    taxon: 32630,
    status: 'provisional',
    primary_identifier: { namespace: 'synthetic_gene', value: 'SYN-N901' },
    is_seed: false,
    derived_from: [],
    ...overrides,
  }
}

/** The seed entity:901, entity:902 and makeGraphEdge's edge between them, answering the default request. */
export function makeNetworkResponse(overrides: Partial<GraphNetworkResponse> = {}): GraphNetworkResponse {
  return {
    api_version: 'graph/v1',
    query: {
      seeds: ['entity:901'],
      layers: null,
      expand: null,
      direction: null,
      outcome: null,
      evidence_basis: null,
      effect: null,
      method: null,
      level: null,
      score: null,
      context: {},
      taxon: null,
      include_partner_edges: null,
      max_edges: null,
      releases: null,
      browser: null,
    },
    nodes: [
      makeNetworkNode({ is_seed: true }),
      makeNetworkNode({ id: 'entity:902', label: 'SYN-N902', primary_identifier: { namespace: 'synthetic_gene', value: 'SYN-N902' } }),
    ],
    edges: [makeGraphEdge()],
    releases: [extrasRelease()],
    page: {
      truncated: false,
      max_edges: 250,
      omitted: {},
      hidden: { supported: 0, tested_absent: 0, inconclusive: 0, not_significant: 0 },
    },
    warnings: [],
    ...overrides,
  }
}

/** Registry v1 has no role-ordered type that is not a reference link. */
export const ligandReceptorType: GraphRelationshipTypeEntry = {
  slug: 'syn_ligand_receptor',
  label: 'Synthetic ligand and receptor',
  definition: '',
  family: 'ligand_receptor',
  orientation: 'role_ordered',
  polarity: 'none',
  allowed_subject_types: ['protein'],
  allowed_object_types: ['protein'],
  subject_role_label: 'ligand',
  object_role_label: 'receptor',
  self_pairs_allowed: false,
  psi_mi_anchors: [],
  display_token: null,
  is_reference: false,
}

/** A type without effect polarity: every supporting result counts as stating no effect. */
function noEffectPolarity(): Pick<GraphEdge, 'effect' | 'effect_counts'> {
  return { effect: 'not_applicable', effect_counts: { activating: 0, inhibiting: 0, unknown: 0, not_stated: 1 } }
}
export const roleOrderedEdge = makeGraphEdge({
  ...noEffectPolarity(),
  id: 'relationship:902',
  type: 'syn_ligand_receptor',
  family: 'ligand_receptor',
  orientation: 'role_ordered',
})
export const directionOnlyEdge = makeGraphEdge({ ...noEffectPolarity(), id: 'relationship:903', type: 'syn_direction_only' })

/** Listed only for a result that does not support it, as an `outcome=all` request lists one. */
function withoutSupport(id: string, outcome: 'not_significant' | 'inconclusive'): GraphEdge {
  const counts = {
    supported: 0,
    tested_absent: 0,
    inconclusive: outcome === 'inconclusive' ? 1 : 0,
    not_significant: outcome === 'not_significant' ? 1 : 0,
  }
  return makeGraphEdge({
    id,
    effect: null,
    effect_counts: { activating: 0, inhibiting: 0, unknown: 0, not_stated: 0 },
    sign_counts: { negative: 0, positive: 0, not_stated: 0 },
    outcome_counts: counts,
    independent_outcome_counts: { ...counts },
    assertion_count: 0,
    evidence_count: 0,
    distinct: { publications: 0, releases: 0, datasets: 0 },
    by_basis: {},
  })
}
export const notSignificantEdge = withoutSupport('relationship:904', 'not_significant')
export const inconclusiveEdge = withoutSupport('relationship:905', 'inconclusive')

/** A test-only genetic type: registry v1 has none. */
function signEdge(id: string, sign: 'negative' | 'positive' | 'mixed'): GraphEdge {
  const n = sign === 'mixed' ? 2 : 1
  return makeGraphEdge({
    id,
    type: 'syn_genetic',
    family: 'genetic',
    orientation: 'symmetric',
    effect: 'not_applicable',
    effect_counts: { activating: 0, inhibiting: 0, unknown: 0, not_stated: n },
    genetic_sign: sign,
    sign_counts: { negative: sign === 'positive' ? 0 : 1, positive: sign === 'negative' ? 0 : 1, not_stated: 0 },
    outcome_counts: { supported: n, tested_absent: 0, inconclusive: 0, not_significant: 0 },
    independent_outcome_counts: { supported: n, tested_absent: 0, inconclusive: 0, not_significant: 0 },
    assertion_count: n,
    evidence_count: n,
    by_basis: { experimental: n },
  })
}
export const negativeSignEdge = signEdge('relationship:906', 'negative')
export const positiveSignEdge = signEdge('relationship:907', 'positive')
export const mixedSignEdge = signEdge('relationship:908', 'mixed')

function physical(): Pick<GraphEdge, 'type' | 'family' | 'orientation' | 'effect' | 'effect_counts'> {
  return { ...noEffectPolarity(), type: 'physical_interaction', family: 'physical', orientation: 'symmetric' }
}
export const levelAssignedEdge = makeGraphEdge({
  ...physical(),
  id: 'relationship:909',
  level: 'association',
  level_counts: { direct_interaction: 0, physical_association: 0, association: 1, not_stated: 0 },
  level_assigned: true,
})
export const unspecifiedEdge = makeGraphEdge({
  ...physical(),
  id: 'relationship:910',
  type: 'protein_interaction_unspecified',
  level: 'not_stated',
  level_counts: { direct_interaction: 0, physical_association: 0, association: 0, not_stated: 1 },
  level_assigned: false,
})
/** Values a deploy can bring before the frontend knows them. */
export const unknownChannelsEdge = makeGraphEdge({
  id: 'relationship:911',
  type: 'syn_unknown_type',
  family: 'syn_family' as string as GraphFamily,
  orientation: 'syn_orientation' as string as GraphOrientation,
  effect: 'syn_effect' as string as GraphEdgeEffect,
  effect_counts: { activating: 0, inhibiting: 0, unknown: 0, not_stated: 1 },
})
export const unknownLevelEdge = makeGraphEdge({
  ...physical(),
  id: 'relationship:912',
  level: 'syn_level' as string as GraphLevel,
  level_counts: { direct_interaction: 0, physical_association: 0, association: 0, not_stated: 0 },
  level_assigned: false,
})
/** Refs of another kind, used verbatim; nodes with no status or primary identifier, as federated ones have. */
export const ppiNodes: GraphNetworkNode[] = [
  makeNetworkNode({ id: 'ppi-protein:1', type: 'protein', label: 'SYN-PPI-1', taxon: null, status: null, primary_identifier: null, is_seed: true }),
  makeNetworkNode({ id: 'ppi-protein:2', type: 'protein', label: 'SYN-PPI-2', taxon: null, status: null, primary_identifier: null }),
]
export const ppiEdge = makeGraphEdge({
  ...physical(),
  id: 'ppi-interaction:42',
  source: 'ppi-protein:1',
  target: 'ppi-protein:2',
  level: 'not_stated',
  level_counts: { direct_interaction: 0, physical_association: 0, association: 0, not_stated: 1 },
  level_assigned: false,
})
/** One, three, five and nine publications: the width grows to its cap at five and stays there. */
export const publicationEdges: GraphEdge[] = [1, 3, 5, 9].map((publications) =>
  makeGraphEdge({ id: `relationship:92${publications}`, distinct: { publications, releases: 1, datasets: 1 } }),
)
/** Its releases name none of its response's, so neither its label nor the legend can name one. */
export const unresolvedReferenceEdge = makeGraphEdge({
  ...noEffectPolarity(),
  id: 'relationship:913',
  type: 'encodes',
  family: 'reference',
  orientation: 'role_ordered',
  releases: ['release:999'],
  distinct: { publications: 0, releases: 1, datasets: 1 },
  by_basis: { unknown: 1 },
})
/**
 * A reference pair that a synthetic release reports only as tested and not detected, as an
 * `outcome=tested_absent` request lists it: its release is listed, yet no release states the link.
 */
export const testedAbsentReferenceEdge = makeGraphEdge({
  id: 'relationship:915',
  type: 'encodes',
  family: 'reference',
  orientation: 'role_ordered',
  effect: 'not_applicable',
  effect_counts: { activating: 0, inhibiting: 0, unknown: 0, not_stated: 0 },
  sign_counts: { negative: 0, positive: 0, not_stated: 0 },
  outcome_counts: { supported: 0, tested_absent: 1, inconclusive: 0, not_significant: 0 },
  independent_outcome_counts: { supported: 0, tested_absent: 1, inconclusive: 0, not_significant: 0 },
  assertion_count: 0,
  evidence_count: 0,
  distinct: { publications: 0, releases: 0, datasets: 0 },
  by_basis: {},
})
/** A second synthetic release, which reports mixedReferenceEdge only as tested and not detected. */
export const testedAbsentRelease: GraphRelease = {
  ...extrasRelease(),
  id: 'release:902',
  dataset: { slug: 'synthetic-extras-tested', title: 'Synthetic tested extras' },
}
/**
 * A reference link that release:901 states and testedAbsentRelease reports only as tested and not
 * detected, as a request for every outcome lists it: it lists both releases, and `distinct.releases`
 * counts only the one that states it, without saying which.
 */
export const mixedReferenceEdge = makeGraphEdge({
  ...noEffectPolarity(),
  id: 'relationship:917',
  type: 'encodes',
  family: 'reference',
  orientation: 'role_ordered',
  releases: ['release:901', 'release:902'],
  outcome_counts: { supported: 1, tested_absent: 1, inconclusive: 0, not_significant: 0 },
  independent_outcome_counts: { supported: 1, tested_absent: 1, inconclusive: 0, not_significant: 0 },
  distinct: { publications: 0, releases: 1, datasets: 1 },
  by_basis: { unknown: 1 },
})

// F3: rows and responses the demonstration cannot produce (panels and table).

export function makeGraphRelease(overrides: Partial<GraphRelease> = {}): GraphRelease {
  return { ...extrasRelease(), ...overrides }
}
export const stagedRelease: GraphRelease = makeGraphRelease({
  id: 'release:903',
  dataset: { slug: 'synthetic-extras-staged', title: 'Synthetic staged extras' },
  version: 'x2',
  status: 'staged',
  licence: { name: 'Test licence', spdx_id: 'CC-BY-4.0', url: 'https://example.org/licence' },
  attribution: 'Test attribution',
  changes_note: 'Hand-written: no import changed it.',
  scopes: ['internal', 'public_view', 'public_bulk'],
})
/** A release that is not synthetic, so the term labels it gives show. */
export const labelledRelease: GraphRelease = makeGraphRelease({
  id: 'release:904',
  dataset: { slug: 'test-labelled', title: 'Test labelled release' },
  version: 'x1',
  is_synthetic: false,
  changes_note: null,
  attribution: '',
})
/** A null model and the not-comparable statement come only from a score type's entry. */
export const testScoreTypes: GraphScoreTypeEntry[] = [
  {
    slug: 'test:p_value', label: 'Test p-value', definition: '', value_kind: 'p_value', direction: 'lower_is_stronger',
    range_min: 0, range_max: 1, unit: null, null_model: null, defining_document: 'https://example.org/scores',
    comparable_across_datasets: false,
  },
  {
    slug: 'test:effect_size', label: 'Test effect size', definition: '', value_kind: 'continuous', direction: 'signed',
    range_min: null, range_max: null, unit: 'log2', null_model: 'multiplicative', defining_document: 'https://example.org/scores',
    comparable_across_datasets: false,
  },
]
/** Registry v1 has no genetic type. */
export const geneticSignType: GraphRelationshipTypeEntry = {
  slug: 'syn_genetic',
  label: 'Synthetic genetic interaction',
  definition: '',
  family: 'genetic',
  orientation: 'symmetric',
  polarity: 'genetic_sign',
  allowed_subject_types: ['gene'],
  allowed_object_types: ['gene'],
  subject_role_label: 'participant',
  object_role_label: 'participant',
  self_pairs_allowed: false,
  psi_mi_anchors: [],
  display_token: null,
  is_reference: false,
}

const NO_FILTERS = { outcome: null, evidence_basis: null, effect: null, method: null, level: null, score: null, context: {} }

/** relationship:901's page, or the page of the edge given: the query and the release counts follow it. */
export function makeRelationshipResponse(overrides: Partial<GraphRelationshipResponse> = {}): GraphRelationshipResponse {
  const edge = overrides.edge ?? makeGraphEdge()
  return {
    api_version: 'graph/v1',
    query: { ...NO_FILTERS, relationship_ref: edge.id, releases: null, browser: null },
    edge,
    definition: 'A hand-written test type.',
    nodes: [
      makeGraphNode({ id: 'entity:901', label: 'SYN-N901', primary_identifier: { namespace: 'synthetic_gene', value: 'SYN-N901' } }),
      makeGraphNode({ id: 'entity:902', label: 'SYN-N902', primary_identifier: { namespace: 'synthetic_gene', value: 'SYN-N902' } }),
    ],
    by_release: [{ release: 'release:901', outcome_counts: { ...edge.outcome_counts } }],
    releases: [makeGraphRelease()],
    ...overrides,
  }
}

export function makeEvidenceResponse(overrides: Partial<GraphEvidenceResponse> = {}): GraphEvidenceResponse {
  return {
    api_version: 'graph/v1',
    query: { ...NO_FILTERS, relationship_ref: 'relationship:901', releases: null, browser: null, after: null, page_size: null },
    evidence: [],
    releases: [],
    page: { page_size: 50, next: null },
    ...overrides,
  }
}

export function makeEntityResponse(overrides: Partial<GraphEntityResponse> = {}): GraphEntityResponse {
  return {
    api_version: 'graph/v1',
    query: { entity_id: 'entity:901', releases: null, browser: null },
    entity: makeGraphNode({ id: 'entity:901', label: 'SYN-N901', primary_identifier: { namespace: 'synthetic_gene', value: 'SYN-N901' } }),
    description: '',
    created_by_release: null,
    identifiers: [{ namespace: 'synthetic_gene', value: 'SYN-N901', version: null, role: 'primary', url: null }],
    reference_relationships: [],
    nodes: [],
    relationship_counts: {},
    releases: [makeGraphRelease()],
    ...overrides,
  }
}

/** No PMID, DOI, PMCID or IMEx id: a made-up one is someone's paper. */
function paper(overrides: Partial<GraphPublication>): GraphPublication {
  return {
    pmid: null, doi: null, pmcid: null, imex_id: null, local_key: null, title: '', authors: '', journal: '', year: null, url: null,
    is_synthetic: true, ...overrides,
  }
}

const EXTRAS_PARTICIPANTS: GraphEvidenceRow['participants'] = [
  { side: 'subject', entity: 'entity:901', stated_entity: null, identifier_as_stated: 'SYN-N901', resolution_basis: 'primary' },
  { side: 'object', entity: 'entity:902', stated_entity: null, identifier_as_stated: 'SYN-N902', resolution_basis: 'primary' },
]

function extrasRow(id: string, overrides: Partial<GraphEvidenceRow>): GraphEvidenceRow {
  return makeGraphEvidenceRow({
    id,
    release: 'release:901',
    record: { key: `extras-${id.split(':')[1]}`, line_number: 2, raw: null },
    participants: EXTRAS_PARTICIPANTS,
    ...overrides,
  })
}

const EXTRAS_PUBLICATIONS: GraphEvidencePublication[] = [
  {
    role: 'reported_in',
    publication: paper({
      local_key: 'extras-paper-1', title: 'Hand-written paper 1 (synthetic: not a real publication)', authors: 'Test A, Test B',
      journal: 'Test Journal', year: 2026, url: 'https://example.org/paper-1',
    }),
  },
  { role: 'method_reference', publication: paper({ local_key: 'extras-method-1' }) },
  { role: 'cited', publication: paper({ local_key: 'extras-cited-1', title: 'Cited test paper', url: 'https://example.org/paper-2' }) },
]

/** MI:0492 is "in vitro", a PSI-MI pseudo-taxon that the backend files under `assay_system`. */
export const regulationRowWithMethods = extrasRow('evidence:951', {
  outcome: 'supported',
  evidence_basis: 'experimental',
  assertion_method: 'manual_assertion',
  effect: 'activating',
  effect_as_stated: 'up-regulates',
  relationship_as_stated: 'activates transcription of',
  methods: [
    { role: 'detection', curie: 'MI:0402', label: null },
    { role: 'participant_identification', curie: 'MI:0102', label: null },
    { role: 'evidence_code', curie: 'ECO:0000353', label: null },
  ],
  eco_term: { curie: 'ECO:0000353', label: null },
  contexts: [
    { facet: 'tissue', term: null, taxon: null, label: 'liver', raw_value: 'liver' },
    { facet: 'host_taxon', term: null, taxon: 32630, label: '', raw_value: '32630' },
    { facet: 'assay_system', term: { curie: 'MI:0492', label: null }, taxon: null, label: '', raw_value: 'MI:0492' },
  ],
  evidence_group: 'g-1',
  qualifiers: { assay_note: 'two replicates' },
  participants: [
    { side: 'subject', entity: 'entity:901', stated_entity: null, identifier_as_stated: 'SYN-N901-ALIAS', resolution_basis: 'alias_unique' },
    { side: 'object', entity: 'entity:902', stated_entity: null, identifier_as_stated: 'SYN-N902', resolution_basis: 'xref_unique' },
  ],
  publications: EXTRAS_PUBLICATIONS,
  record: { key: 'extras-01', line_number: 2, raw: { record_id: 'extras-01', effect: 'activating', note: 'line one\nline two' } },
})
/** It fails the first criterion of its threshold. */
export const notSignificantRow = extrasRow('evidence:953', {
  outcome: 'not_significant',
  evidence_basis: 'statistical',
  assertion_method: 'automatic_assertion',
  effect: null,
  genetic_sign: null,
  scores: [
    { type: 'test:p_value', label: 'Test p-value', value: null, raw_value: '<2.2e-16', value_kind: 'p_value', direction: 'lower_is_stronger', unit: null },
    { type: 'test:effect_size', label: 'Test effect size', value: 0.12, raw_value: '0.12', value_kind: 'continuous', direction: 'signed', unit: 'log2' },
  ],
  qualifiers: {
    threshold: [
      { score_type: 'test:effect_size', operator: 'abs>=', value: '0.5' },
      { score_type: 'test:p_value', operator: '<', value: '0.05' },
    ],
  },
})
export const inconclusiveRow = extrasRow('evidence:954', {
  outcome: 'inconclusive', evidence_basis: 'computational_inference', assertion_method: 'unknown', effect: null,
})
export const textMiningRow = extrasRow('evidence:955', {
  outcome: 'supported',
  evidence_basis: 'text_mining',
  assertion_method: 'automatic_assertion',
  effect: null,
  participants: [
    { side: 'subject', entity: 'entity:901', stated_entity: null, identifier_as_stated: 'SYN-N901', resolution_basis: 'source_local' },
    { side: 'object', entity: 'entity:902', stated_entity: null, identifier_as_stated: 'SYN-N902', resolution_basis: 'primary' },
  ],
})
export const unknownBasisRow = extrasRow('evidence:956', {
  outcome: 'supported', evidence_basis: 'unknown', assertion_method: 'unknown', effect: 'unknown',
})
export const labelledPhysicalRow = extrasRow('evidence:961', {
  release: 'release:904',
  outcome: 'supported',
  evidence_basis: 'experimental',
  assertion_method: 'manual_assertion',
  effect: null,
  interaction_type: { curie: 'MI:0915', label: 'physical association' },
  interaction_level: 'physical_association',
  methods: [{ role: 'detection', curie: 'MI:0018', label: 'two hybrid' }],
  eco_term: { curie: 'ECO:0000353', label: 'physical interaction evidence used in manual assertion' },
  participants: [
    { side: 'subject', entity: 'entity:901', stated_entity: 'entity:903', identifier_as_stated: 'SYN-N903', resolution_basis: 'isoform_entry' },
    { side: 'object', entity: 'entity:902', stated_entity: null, identifier_as_stated: 'SYN-N902-CURATED', resolution_basis: 'curated' },
  ],
  publications: [],
})
/** openPIP's assignment: no interaction type is stated. */
export const assignedAssociationRow = extrasRow('evidence:962', {
  outcome: 'supported', effect: null, interaction_type: null, interaction_level: 'association',
})
/** A crosswalk term keeps its pinned label on a synthetic release. */
export const statedAssociationRow = extrasRow('evidence:963', {
  outcome: 'supported', effect: null, interaction_type: { curie: 'MI:0914', label: 'association' }, interaction_level: 'association',
})
export const signRow = extrasRow('evidence:971', {
  outcome: 'supported',
  effect: null,
  genetic_sign: 'negative',
  scores: [{ type: 'test:effect_size', label: 'Test effect size', value: -0.4, raw_value: '-0.4', value_kind: 'continuous', direction: 'signed', unit: 'log2' }],
})

const REGULATION_COUNTS = { supported: 3, tested_absent: 0, inconclusive: 1, not_significant: 1 }
export const regulationRelationshipPage = makeRelationshipResponse({
  edge: makeGraphEdge({
    outcome_counts: { ...REGULATION_COUNTS },
    independent_outcome_counts: { ...REGULATION_COUNTS },
    effect_counts: { activating: 1, inhibiting: 0, unknown: 1, not_stated: 1 },
    sign_counts: { negative: 0, positive: 0, not_stated: 3 },
    assertion_count: 3,
    evidence_count: 3,
    by_basis: { experimental: 1, text_mining: 1, unknown: 1 },
  }),
  by_release: [{ release: 'release:901', outcome_counts: { ...REGULATION_COUNTS } }],
})
export const regulationEvidencePage = makeEvidenceResponse({
  evidence: [regulationRowWithMethods, notSignificantRow, inconclusiveRow, textMiningRow, unknownBasisRow],
  releases: [makeGraphRelease()],
})
/** A physical type joins proteins or isoforms (registry v1), so its page's ends are protein entries. */
function proteinEnds(): GraphNode[] {
  return ['901', '902'].map((n) => makeGraphNode({
    id: `entity:${n}`, type: 'protein', label: `SYN-N${n}`, primary_identifier: { namespace: 'synthetic_protein', value: `SYN-N${n}` },
  }))
}
const PHYSICAL_COUNTS = { supported: 3, tested_absent: 0, inconclusive: 0, not_significant: 0 }
export const physicalRelationshipPage = makeRelationshipResponse({
  edge: makeGraphEdge({
    ...physical(),
    id: 'relationship:930',
    releases: ['release:901', 'release:904'],
    level: 'physical_association',
    level_counts: { direct_interaction: 0, physical_association: 1, association: 2, not_stated: 0 },
    level_assigned: false,
    outcome_counts: { ...PHYSICAL_COUNTS },
    independent_outcome_counts: { ...PHYSICAL_COUNTS },
    effect_counts: { activating: 0, inhibiting: 0, unknown: 0, not_stated: 3 },
    sign_counts: { negative: 0, positive: 0, not_stated: 3 },
    assertion_count: 3,
    evidence_count: 3,
    distinct: { publications: 0, releases: 2, datasets: 2 },
    by_basis: { experimental: 3 },
  }),
  nodes: proteinEnds(),
  by_release: [
    { release: 'release:901', outcome_counts: { supported: 2, tested_absent: 0, inconclusive: 0, not_significant: 0 } },
    { release: 'release:904', outcome_counts: { supported: 1, tested_absent: 0, inconclusive: 0, not_significant: 0 } },
  ],
  releases: [makeGraphRelease(), labelledRelease],
})
export const physicalEvidencePage = makeEvidenceResponse({
  query: { ...makeEvidenceResponse().query, relationship_ref: 'relationship:930' },
  evidence: [labelledPhysicalRow, assignedAssociationRow, statedAssociationRow],
  releases: [makeGraphRelease(), labelledRelease],
})
export const geneticRelationshipPage = makeRelationshipResponse({ edge: negativeSignEdge })
export const geneticEvidencePage = makeEvidenceResponse({
  query: { ...makeEvidenceResponse().query, relationship_ref: 'relationship:906' },
  evidence: [signRow],
  releases: [makeGraphRelease()],
})
/** The three evidence pages, each served by its own relationship's ref. */
export const extrasEvidencePage: GraphEvidenceResponse[] = [regulationEvidencePage, physicalEvidencePage, geneticEvidencePage]
export const levelAssignedPage = makeRelationshipResponse({ edge: levelAssignedEdge, nodes: proteinEnds() })
export const unspecifiedPage = makeRelationshipResponse({ edge: unspecifiedEdge, nodes: proteinEnds() })

/** Listed only as tested and not detected at a level not stated: a type that claims no physical contact. */
export const unspecifiedTestedAbsentEdge = makeGraphEdge({
  ...physical(),
  id: 'relationship:933',
  type: 'protein_interaction_unspecified',
  effect_counts: { activating: 0, inhibiting: 0, unknown: 0, not_stated: 0 },
  sign_counts: { negative: 0, positive: 0, not_stated: 0 },
  outcome_counts: { supported: 0, tested_absent: 1, inconclusive: 0, not_significant: 0 },
  independent_outcome_counts: { supported: 0, tested_absent: 1, inconclusive: 0, not_significant: 0 },
  assertion_count: 0,
  evidence_count: 0,
  distinct: { publications: 0, releases: 0, datasets: 0 },
  by_basis: {},
  level: null,
  level_counts: { direct_interaction: 0, physical_association: 0, association: 0, not_stated: 0 },
  level_assigned: null,
  tested_levels: { tested_absent: ['not_stated'] },
})
/** Two relationships of one type and ends, whose names only their refs tell apart. */
export const twinEdges: GraphEdge[] = [makeGraphEdge({ id: 'relationship:931' }), makeGraphEdge({ id: 'relationship:932' })]

/** One string per source field, named by the field's path: a field that never renders fails its test. */
export const hostile = (field: string): string => `<img data-hostile="${field}" src="x"><b data-hostile="${field}">${field}</b>`

const hostileRelease = makeGraphRelease({
  id: 'release:905',
  dataset: { slug: 'syn-hostile', title: hostile('dataset.title') },
  version: hostile('version'),
  licence: { name: hostile('licence.name'), spdx_id: hostile('licence.spdx_id'), url: 'javascript:alert(1)' },
  attribution: hostile('attribution'),
  changes_note: hostile('changes_note'),
})
export const hostileRelationshipPage = makeRelationshipResponse({
  edge: makeGraphEdge({
    releases: ['release:905'],
    qualifiers: {
      [hostile('qualifiers.key')]: hostile('qualifiers.value'),
      obj: { dangerouslySetInnerHTML: { __html: '<em data-hostile="edge">z</em>' } },
    },
  }),
  definition: hostile('definition'),
  nodes: [
    makeGraphNode({ id: 'entity:901', label: `SYN-${hostile('nodes[0].label')}`, primary_identifier: null }),
    makeGraphNode({ id: 'entity:902', label: `SYN-${hostile('nodes[1].label')}`, primary_identifier: null }),
  ],
  by_release: [{ release: 'release:905', outcome_counts: { supported: 1, tested_absent: 0, inconclusive: 0, not_significant: 0 } }],
  releases: [hostileRelease],
})
export const hostileEvidencePage = makeEvidenceResponse({
  evidence: [
    makeGraphEvidenceRow({
      id: 'evidence:970',
      release: 'release:905',
      effect_as_stated: hostile('effect_as_stated'),
      relationship_as_stated: hostile('relationship_as_stated'),
      participants: [
        { side: 'subject', entity: 'entity:901', stated_entity: null, identifier_as_stated: hostile('participants[0].identifier_as_stated'), resolution_basis: 'primary' },
        { side: 'object', entity: 'entity:902', stated_entity: null, identifier_as_stated: hostile('participants[1].identifier_as_stated'), resolution_basis: 'primary' },
      ],
      methods: [{ role: 'detection', curie: 'MI:0018', label: hostile('methods[0].label') }],
      scores: [{
        type: 'test:effect_size', label: hostile('scores[0].label'), value: null, raw_value: hostile('scores[0].raw_value'),
        value_kind: 'continuous', direction: 'signed', unit: hostile('scores[0].unit'),
      }],
      contexts: [{ facet: 'tissue', term: null, taxon: null, label: hostile('contexts[0].label'), raw_value: hostile('contexts[0].raw_value') }],
      evidence_group: hostile('evidence_group'),
      publications: [{
        role: 'reported_in',
        publication: paper({
          local_key: 'extras-hostile-1', title: hostile('publications[0].title'), authors: hostile('publications[0].authors'),
          journal: hostile('publications[0].journal'), url: 'javascript:alert(1)',
        }),
      }],
      record: {
        key: hostile('record.key'),
        line_number: 3,
        raw: {
          [hostile('raw.key')]: hostile('raw.value'),
          nested: { dangerouslySetInnerHTML: { __html: '<em data-hostile="raw">x</em>' } },
          long: 'x'.repeat(10_000),
        },
      },
      qualifiers: {
        note: hostile('row.qualifiers.note'),
        obj: { dangerouslySetInnerHTML: { __html: '<em data-hostile="q">y</em>' } },
      },
    }),
  ],
  releases: [hostileRelease],
})
/** A regulation row carries no interaction type or ECO label, so a physical row holds those two. */
export const hostilePhysicalRow = extrasRow('evidence:972', {
  outcome: 'supported',
  effect: null,
  interaction_type: { curie: 'MI:0915', label: hostile('interaction_type.label') },
  interaction_level: 'physical_association',
  eco_term: { curie: 'ECO:0000353', label: hostile('eco_term.label') },
})
export const hostilePhysicalEvidencePage = makeEvidenceResponse({
  query: { ...makeEvidenceResponse().query, relationship_ref: 'relationship:930' },
  evidence: [hostilePhysicalRow],
  releases: [makeGraphRelease()],
})
export const hostileNetwork = makeNetworkResponse({
  nodes: [
    makeNetworkNode({ is_seed: true, label: `SYN-${hostile('nodes[0].label')}`, primary_identifier: null }),
    makeNetworkNode({ id: 'entity:902', label: `SYN-${hostile('nodes[1].label')}`, primary_identifier: null }),
  ],
})

function entityWithStatus(status: GraphEntityStatus | null): GraphNode {
  return makeGraphNode({
    id: 'entity:901', label: 'SYN-N901', status, primary_identifier: { namespace: 'synthetic_gene', value: 'SYN-N901' },
  })
}
export const activeEntityPage = makeEntityResponse({
  entity: entityWithStatus('active'),
  description: 'A hand-written test gene.',
  created_by_release: 'release:901',
  identifiers: [
    { namespace: 'synthetic_gene', value: 'SYN-N901', version: null, role: 'primary', url: null },
    { namespace: 'synthetic_gene', value: 'SYN-N901-ALT', version: '2', role: 'secondary', url: 'https://example.org/entity/SYN-N901' },
    { namespace: 'synthetic_gene', value: 'SYN-N901-SYM', version: null, role: 'symbol', url: null },
  ],
})
export const retiredEntityPage = makeEntityResponse({ entity: entityWithStatus('retired') })
export const mergedEntityPage = makeEntityResponse({ entity: entityWithStatus('merged') })
export const nullStatusEntityPage = makeEntityResponse({ entity: entityWithStatus(null) })
/** A status a deploy can bring before the frontend knows it. */
export const unknownStatusEntityPage = makeEntityResponse({ entity: entityWithStatus('syn_status' as string as GraphEntityStatus) })
const hostileEntity = makeGraphNode({
  id: 'entity:901', label: `SYN-${hostile('entity.label')}`, primary_identifier: { namespace: 'synthetic_gene', value: 'SYN-N901' },
})
/** Its relationship count's key holds no `_`, since the panel words the key from its slug. */
export const hostileEntityPage = makeEntityResponse({
  entity: hostileEntity,
  description: hostile('description'),
  identifiers: [
    { namespace: 'synthetic_gene', value: hostile('identifiers.value'), version: hostile('identifiers.version'), role: 'secondary', url: 'javascript:alert(1)' },
  ],
  relationship_counts: { [hostile('relationshipCounts.key')]: 1 },
  reference_relationships: [
    makeGraphEdge({ ...noEffectPolarity(), id: 'relationship:934', type: 'encodes', family: 'reference', orientation: 'role_ordered' }),
  ],
  // An encodes relationship's object is a protein entry.
  nodes: [hostileEntity, makeGraphNode({ id: 'entity:902', type: 'protein', label: `SYN-${hostile('nodes[1].label')}`, primary_identifier: null })],
})

// F4: browser instances the demonstration cannot produce (gallery and instance pages). Each follows
// browsers.availability.compute() but unknownCodesInstance, which carries a deploy's early codes.

/** The extras dataset's earlier version, superseded: an instance pinned to it stays live (PAGE:1740-1742). */
export const supersededRelease: GraphRelease = makeGraphRelease({
  id: 'release:906', version: 'x0', status: 'superseded', superseded: true,
})

/** What a selection can name, in id order, as browsers/{slug} lists its releases (TYPES:840-841); hostileRelease is F3's. */
const INSTANCE_RELEASES: readonly GraphRelease[] = [makeGraphRelease(), stagedRelease, labelledRelease, hostileRelease, supersededRelease]

const instancePaper = paper({ local_key: 'extras-instance-paper', title: 'SYN instance paper (synthetic: not a real publication)', year: 2026 })

/** A typed, available, public instance that selects release:901, live for every caller: browser-typed's keys. */
export function makeInstance(overrides: Partial<GraphInstance> = {}): GraphInstance {
  return {
    slug: 'syn-extra',
    title: 'SYN extra browser',
    short_title: 'SYN extra',
    summary: 'A hand-written test instance.',
    kind: 'typed',
    status: 'available',
    visibility: 'public',
    availability: 'live',
    is_synthetic: true,
    external_url: null,
    tags: ['gene_regulation'],
    taxa: [32630],
    primary_taxon: 32630,
    relationship_types: ['transcriptional_regulation'],
    assays: [],
    publications: [],
    description: '',
    methods_text: '',
    stated_entity_types: [],
    selection: [{ release: 'release:901', role: 'primary' }],
    presentation: {},
    allow_external_services: false,
    external_services_effective: false,
    ...overrides,
  }
}

/** The gallery's keys of an instance, in the serializer's order (backend/browsers/api.py:294-319). */
export function instanceItem(instance: GraphInstance): GraphInstanceItem {
  return {
    slug: instance.slug, title: instance.title, short_title: instance.short_title, summary: instance.summary,
    kind: instance.kind, status: instance.status, visibility: instance.visibility, availability: instance.availability,
    is_synthetic: instance.is_synthetic, external_url: instance.external_url, tags: instance.tags, taxa: instance.taxa,
    primary_taxon: instance.primary_taxon, relationship_types: instance.relationship_types, assays: instance.assays,
    publications: instance.publications,
  }
}

/** browsers/{slug}'s answer: the releases the selection names, in id order. */
export function makeBrowserResponse(instance: GraphInstance): GraphBrowserResponse {
  const chosen = new Set(instance.selection.map((entry) => entry.release))
  return {
    api_version: 'graph/v1',
    query: { slug: instance.slug },
    instance,
    releases: INSTANCE_RELEASES.filter((release) => chosen.has(release.id)),
  }
}

/** The gallery's answer with every filter unset; a non-null `next` is a page v1 never sends (W35). */
export function makeBrowsersResponse(instances: readonly GraphInstance[], next: string | null = null): GraphBrowsersResponse {
  return {
    api_version: 'graph/v1',
    query: { q: null, tag: null, relationship_type: null, taxon: null, assay: null, year: null, status: null, availability: null },
    instances: instances.map(instanceItem),
    page: { next },
  }
}

export const plannedInstance = makeInstance({
  slug: 'syn-planned', title: 'SYN planned browser', status: 'planned', availability: 'planned', publications: [instancePaper],
})
export const plannedPlainInstance = makeInstance({
  slug: 'syn-planned-plain', title: 'SYN planned plain browser', status: 'planned', availability: 'planned', is_synthetic: false,
  selection: [],
})
export const retiredInstance = makeInstance({ slug: 'syn-retired', title: 'SYN retired browser', status: 'retired', availability: 'retired' })
export const externalInstance = makeInstance({
  slug: 'syn-external', title: 'SYN external portal', kind: 'external', availability: 'external', is_synthetic: false,
  external_url: 'https://portal.example/browse', tags: ['genetic_interaction'], taxa: [7227, 9606], primary_taxon: 7227,
  relationship_types: [], selection: [],
})
export const citationCardInstance = makeInstance({
  slug: 'syn-card', title: 'SYN citation card', kind: 'metadata_only', availability: 'metadata_only', relationship_types: [],
  publications: [instancePaper], selection: [],
})
/** Available and typed, selecting nothing: "data unavailable" (PAGE:1760). */
export const unavailableInstance = makeInstance({
  slug: 'syn-unavailable', title: 'SYN unavailable browser', availability: 'metadata_only', is_synthetic: false, selection: [],
})
export const plannedPortalInstance = makeInstance({
  slug: 'syn-portal-planned', title: 'SYN planned portal search', kind: 'portal_ppi', status: 'planned', availability: 'planned',
  is_synthetic: false, tags: ['protein_interaction'], taxa: [], primary_taxon: null, relationship_types: [], selection: [],
})
export const retiredPortalInstance = makeInstance({
  ...plannedPortalInstance, slug: 'syn-portal-retired', title: 'SYN retired portal search', status: 'retired', availability: 'retired',
})
export const unlistedInstance = makeInstance({ slug: 'syn-unlisted', title: 'SYN unlisted browser', visibility: 'unlisted' })
/** A staged selected release keeps the instance metadata_only for staff too (PAGE:1762-1764). */
export const stagedInstance = makeInstance({
  slug: 'syn-staged', title: 'SYN staged browser', visibility: 'staff', availability: 'metadata_only',
  selection: [{ release: 'release:903', role: 'primary' }],
})
/** Selection order differs from id order; the context release is not synthetic, the primary one superseded. */
export const twoReleaseInstance = makeInstance({
  slug: 'syn-two', title: 'SYN two-release browser',
  selection: [{ release: 'release:906', role: 'primary' }, { release: 'release:904', role: 'context' }],
})
export const papersInstance = makeInstance({
  slug: 'syn-papers', title: 'SYN papers browser',
  publications: [EXTRAS_PUBLICATIONS[0].publication, paper({ local_key: 'extras-untitled', title: '', year: null })],
})
/** Not flagged, its release not synthetic, its paper synthetic: the badge must still show (E4). */
export const syntheticPaperInstance = makeInstance({
  slug: 'syn-paper-only', title: 'SYN synthetic-paper browser', is_synthetic: false, publications: [instancePaper],
  selection: [{ release: 'release:904', role: 'primary' }],
})
export const plainInstance = makeInstance({
  slug: 'syn-plain', title: 'SYN plain browser', is_synthetic: false, selection: [{ release: 'release:904', role: 'primary' }],
})
export const crossTaxonInstance = makeInstance({ slug: 'syn-cross', title: 'SYN cross-taxon browser', taxa: [7227, 9606], primary_taxon: 7227 })
/** Its 2019 paper beside the 2026 ones lets a wrong year order fail (AD-6). */
export const primaryOutsideInstance = makeInstance({
  slug: 'syn-primary-outside', title: 'SYN primary-outside browser', taxa: [32630], primary_taxon: 9606,
  publications: [paper({ local_key: 'extras-older-paper', title: 'SYN older paper (synthetic: not a real publication)', year: 2019 })],
})
export const unknownTaxonInstance = makeInstance({
  slug: 'syn-unknown-taxon', title: 'SYN unknown-taxon browser', taxa: [424242], primary_taxon: null,
})
/** One string per field, named by its path, and addresses that must never reach href. */
export const hostileInstance = makeInstance({
  slug: 'syn-hostile', title: hostile('title'), short_title: hostile('short_title'), summary: hostile('summary'),
  description: `${hostile('description')}\nSecond line of the description.`, methods_text: hostile('methods_text'),
  kind: 'external', availability: 'external', external_url: 'javascript:alert(1)',
  tags: [hostile('tags[0]')], relationship_types: [hostile('relationship_types[0]')],
  assays: [{ curie: 'MI:0018', label: hostile('assays[0].label') }],
  publications: [paper({
    pmid: hostile('pmid'), doi: hostile('doi'), pmcid: hostile('pmcid'), imex_id: hostile('imex_id'), local_key: hostile('local_key'),
    title: hostile('publications[0].title'), authors: hostile('publications[0].authors'),
    journal: hostile('publications[0].journal'), url: 'data:text/html,<b data-hostile="url">x</b>',
  })],
  selection: [],
})
/** Typed and live, selecting F3's hostile release, with paper addresses that isHttpUrl refuses and React writes as given. */
export const hostileTypedInstance = makeInstance({
  slug: 'syn-hostile-typed', title: 'SYN hostile typed browser', selection: [{ release: 'release:905', role: 'primary' }],
  publications: [
    paper({ local_key: 'extras-spaced-url', title: 'SYN spaced-address paper (synthetic: not a real publication)', url: ' https://example.org/x' }),
    paper({ local_key: 'extras-relative-url', title: 'SYN protocol-relative paper (synthetic: not a real publication)', url: '//example.org/x' }),
    paper({ local_key: 'extras-handler-url', title: 'SYN protocol-handler paper (synthetic: not a real publication)', url: 'ms-msdt:/id x' }),
  ],
})
/** Codes a deploy can bring before the frontend knows them (TYPES:15-16). */
export const unknownCodesInstance = makeInstance({
  slug: 'syn-unknown-codes', title: 'SYN unknown-codes browser',
  kind: 'syn_kind' as string as GraphInstanceKind,
  status: 'syn_status' as string as GraphInstanceStatus,
  visibility: 'syn_visibility' as string as GraphInstanceVisibility,
  availability: 'syn_availability' as string as GraphAvailability,
  tags: ['syn_tag'],
  selection: [{ release: 'release:901', role: 'syn_role' as string as GraphSelectionRole }],
})

// X1: networks and searches of the unified explorer that the demonstration cannot produce. Networks
// take refs from 980 and searches refs from 910 and from 1001, none of them an earlier shape's.

/** The release that states sharedEntryNetwork's encodes links; the words name it by title and version. */
export const mappingRelease: GraphRelease = makeGraphRelease({
  id: 'release:908',
  dataset: { slug: 'synthetic-extras-mapping', title: 'Synthetic extras mapping' },
})
/** The release that states sharedEntryNetwork's isoform_of link. */
export const isoformRelease: GraphRelease = makeGraphRelease({
  id: 'release:909',
  dataset: { slug: 'synthetic-extras-isoforms', title: 'Synthetic extras isoforms' },
})

/** A network node with these keys: a gene names a synthetic_gene identifier, any other type a synthetic_protein one. */
function expandedNode(
  id: string,
  type: string,
  label: string,
  derivedFrom: GraphNodeDerivation[] = [],
  isSeed = false,
): GraphNetworkNode {
  const namespace = type === 'gene' ? 'synthetic_gene' : 'synthetic_protein'
  return makeNetworkNode({
    id, type, label, primary_identifier: { namespace, value: label }, is_seed: isSeed, derived_from: derivedFrom,
  })
}

/** A reference link that `release` states, as a network with `expand` lists it: summarised under the default filter. */
function referenceLink(
  id: string,
  type: 'encodes' | 'isoform_of',
  source: string,
  target: string,
  release = 'release:901',
): GraphEdge {
  return makeGraphEdge({
    ...noEffectPolarity(),
    id,
    type,
    family: 'reference',
    orientation: 'role_ordered',
    source,
    target,
    releases: [release],
    distinct: { publications: 0, releases: 1, datasets: 1 },
    by_basis: { unknown: 1 },
  })
}

/**
 * Gene entity:980 named: the entry it encodes (entity:981) and that entry's isoform (entity:982) are
 * derived seeds, each listing its path; the entry's physical partner (entity:983) and the gene's
 * target (entity:984) are partners; the two reference links follow the ranked edges, by id.
 */
export const derivedSeedNetwork: GraphNetworkResponse = makeNetworkResponse({
  query: { ...makeNetworkResponse().query, seeds: ['entity:980'], expand: 'reference' },
  nodes: [
    expandedNode('entity:980', 'gene', 'SYN-N980', [], true),
    expandedNode('entity:981', 'protein', 'SYN-N981', [{ seed: 'entity:980', via: ['relationship:980'] }]),
    expandedNode('entity:982', 'protein_isoform', 'SYN-N981-1', [
      { seed: 'entity:980', via: ['relationship:980', 'relationship:981'] },
    ]),
    expandedNode('entity:983', 'protein', 'SYN-N983'),
    expandedNode('entity:984', 'gene', 'SYN-N984'),
  ],
  edges: [
    makeGraphEdge({
      ...physical(),
      id: 'relationship:982',
      source: 'entity:981',
      target: 'entity:983',
      level: 'not_stated',
      level_counts: { direct_interaction: 0, physical_association: 0, association: 0, not_stated: 1 },
      level_assigned: false,
    }),
    makeGraphEdge({ id: 'relationship:983', source: 'entity:980', target: 'entity:984' }),
    referenceLink('relationship:980', 'encodes', 'entity:980', 'entity:981'),
    referenceLink('relationship:981', 'isoform_of', 'entity:982', 'entity:981'),
  ],
})

/**
 * derivedSeedNetwork with derived_seeds 6 and its warning: a stand-in for the notice's line only. As the
 * warning says, the API omits derived seeds because a network looks up at most 26 seeds, named and derived
 * together, or because a step of the closure reached its row bound: an entry with thirty isoforms keeps 25
 * and drops 6, a cut step adds one, and at the shipped bounds a cut comes only past a full cap, so no
 * answer pairs these two nodes with this count.
 */
export const closureDropNetwork: GraphNetworkResponse = makeNetworkResponse({
  ...derivedSeedNetwork,
  page: {
    truncated: true,
    max_edges: 250,
    omitted: { derived_seeds: 6 },
    hidden: { supported: 0, tested_absent: 0, inconclusive: 0, not_significant: 0 },
  },
  warnings: [
    {
      code: 'derived_seeds_truncated',
      detail: '6 derived seeds omitted: a network looks up at most 26 seeds, named and derived together, or a step of the closure reached its row bound.',
    },
  ],
})

/**
 * Entry entity:985 named, encoded by three genes (entity:986 to entity:988), the case of one entry and
 * several genes (PLAN:152's histone): each gene is a derived seed, and so is the isoform entity:989.
 * mappingRelease states the encodes links and isoformRelease the isoform_of link, so each link's
 * label names its own release.
 */
export const sharedEntryNetwork: GraphNetworkResponse = makeNetworkResponse({
  query: { ...makeNetworkResponse().query, seeds: ['entity:985'], expand: 'reference' },
  nodes: [
    expandedNode('entity:985', 'protein', 'SYN-N985', [], true),
    expandedNode('entity:986', 'gene', 'SYN-N986', [{ seed: 'entity:985', via: ['relationship:984'] }]),
    expandedNode('entity:987', 'gene', 'SYN-N987', [{ seed: 'entity:985', via: ['relationship:985'] }]),
    expandedNode('entity:988', 'gene', 'SYN-N988', [{ seed: 'entity:985', via: ['relationship:986'] }]),
    expandedNode('entity:989', 'protein_isoform', 'SYN-N985-1', [{ seed: 'entity:985', via: ['relationship:987'] }]),
  ],
  edges: [
    referenceLink('relationship:984', 'encodes', 'entity:986', 'entity:985', 'release:908'),
    referenceLink('relationship:985', 'encodes', 'entity:987', 'entity:985', 'release:908'),
    referenceLink('relationship:986', 'encodes', 'entity:988', 'entity:985', 'release:908'),
    referenceLink('relationship:987', 'isoform_of', 'entity:989', 'entity:985', 'release:909'),
  ],
  releases: [mappingRelease, isoformRelease],
})

/**
 * derivedSeedNetwork under `method=MI:0018`: the physical edge, whose evidence names that detection
 * method, is ranked, and the reference links stay, since the closure reads no request filter; the
 * regulation edge, whose evidence names no method, and its target are gone.
 */
export const methodFilterNetwork: GraphNetworkResponse = makeNetworkResponse({
  ...derivedSeedNetwork,
  query: { ...derivedSeedNetwork.query, method: ['MI:0018'] },
  nodes: derivedSeedNetwork.nodes.filter((node) => node.id !== 'entity:984'),
  edges: derivedSeedNetwork.edges.filter((edge) => edge.id !== 'relationship:983'),
})

function extrasGene(id: string, label: string, value = label): GraphNode {
  return makeGraphNode({ id, label, primary_identifier: { namespace: 'synthetic_gene', value } })
}

function primaryMatch(value: string): GraphMatch {
  return { kind: 'primary', namespace: 'synthetic_gene', value, case_exact: true, version_mismatch: false }
}

function aliasMatch(value: string): GraphMatch {
  return { kind: 'alias', namespace: 'synthetic_gene', value, case_exact: true, version_mismatch: false }
}

function labelMatch(value: string): GraphMatch {
  return { kind: 'label', namespace: null, value, case_exact: true, version_mismatch: false }
}

/** entity:901 as a candidate of a term, matched by its primary identifier, unless told otherwise. */
export function makeResolveCandidate(overrides: Partial<GraphCandidate> = {}): GraphCandidate {
  return {
    entity: extrasGene('entity:901', 'SYN-N901'),
    exact_primary: true,
    matches: [primaryMatch('SYN-N901')],
    releases: ['release:901'],
    ...overrides,
  }
}

function unmatchedResult(): Omit<GraphResolveResult, 'term'> {
  return { status: 'unmatched', truncated: false, candidates: [] }
}

/** resolve's answer for these terms, in order: each term's result from `results`, unmatched otherwise. */
export function makeResolveResponse(
  terms: readonly string[],
  results: Readonly<Record<string, Omit<GraphResolveResult, 'term'>>> = {},
  overrides: Partial<GraphResolveResponse> = {},
): GraphResolveResponse {
  const found = terms.map((term): GraphResolveResult => ({ term, ...(results[term] ?? unmatchedResult()) }))
  return {
    api_version: 'graph/v1',
    query: { q: [...terms], namespace: null, taxon: null, entity_type: null, releases: null, browser: null },
    results: found,
    releases: found.some((result) => result.candidates.length > 0) ? [makeGraphRelease()] : [],
    page: { truncated: false, max_candidates: 20, omitted: {} },
    warnings: [],
    ...overrides,
  }
}

function namespaceQuery(term: string): GraphResolveResponse['query'] {
  return { q: [term], namespace: 'synthetic_gene', taxon: null, entity_type: null, releases: null, browser: null }
}

/** Under `namespace=synthetic_gene`, one entity's alias: a candidate to confirm, never resolved. */
export const namespaceAliasResolve: GraphResolveResponse = makeResolveResponse(
  ['SYN-N944-ALIAS'],
  {
    'SYN-N944-ALIAS': {
      status: 'candidate',
      truncated: false,
      candidates: [
        makeResolveCandidate({
          entity: extrasGene('entity:944', 'SYN-N944'), exact_primary: false, matches: [aliasMatch('SYN-N944-ALIAS')],
        }),
      ],
    },
  },
  { query: namespaceQuery('SYN-N944-ALIAS') },
)

/** Under `namespace=synthetic_gene`, an alias two entities share: ambiguous, both listed. */
export const namespaceAmbiguousResolve: GraphResolveResponse = makeResolveResponse(
  ['SYN-N-SHARED-ALIAS'],
  {
    'SYN-N-SHARED-ALIAS': {
      status: 'ambiguous',
      truncated: false,
      candidates: ['entity:945', 'entity:946'].map((id) =>
        makeResolveCandidate({
          entity: extrasGene(id, `SYN-N${id.slice('entity:'.length)}`),
          exact_primary: false,
          matches: [aliasMatch('SYN-N-SHARED-ALIAS')],
        }),
      ),
    },
  },
  { query: namespaceQuery('SYN-N-SHARED-ALIAS') },
)

/** Twenty-five terms, resolve's cap on one request: SYN-M01 to SYN-M25. */
export const MANY_SEARCH_TERMS: readonly string[] = Array.from(
  { length: 25 },
  (_, index) => `SYN-M${String(index + 1).padStart(2, '0')}`,
)

function manyResults(): Record<string, Omit<GraphResolveResult, 'term'>> {
  const results: Record<string, Omit<GraphResolveResult, 'term'>> = {}
  MANY_SEARCH_TERMS.slice(0, 11).forEach((term, index) => {
    results[term] = {
      status: 'resolved',
      truncated: false,
      candidates: [makeResolveCandidate({ entity: extrasGene(`entity:${910 + index}`, term), matches: [primaryMatch(term)] })],
    }
  })
  results['SYN-M12'] = {
    status: 'candidate',
    truncated: false,
    candidates: [
      makeResolveCandidate({
        entity: extrasGene('entity:921', 'SYN-M12', 'SYN-G921'), exact_primary: false, matches: [labelMatch('SYN-M12')],
      }),
    ],
  }
  results['SYN-M13'] = {
    status: 'ambiguous',
    truncated: false,
    candidates: ['entity:922', 'entity:923'].map((id) =>
      makeResolveCandidate({
        entity: extrasGene(id, 'SYN-M13', `SYN-G${id.slice('entity:'.length)}`),
        exact_primary: false,
        matches: [labelMatch('SYN-M13')],
      }),
    ),
  }
  return results
}

/**
 * Their answer: SYN-M01 to SYN-M11 resolved (entity:910 to entity:920), one more than max_seeds
 * allows; SYN-M12 a candidate by its label (entity:921); SYN-M13 ambiguous, a label two genes share
 * (entity:922 and entity:923); the other twelve unmatched.
 */
export const manyTermResolve: GraphResolveResponse = makeResolveResponse(MANY_SEARCH_TERMS, manyResults())

/** One label more than twenty entities share: twenty are listed, and the page says one term was cut. */
export const cutResolve: GraphResolveResponse = makeResolveResponse(
  ['SYN-CUT'],
  {
    'SYN-CUT': {
      status: 'ambiguous',
      truncated: true,
      candidates: Array.from({ length: 20 }, (_, index) =>
        makeResolveCandidate({
          entity: extrasGene(`entity:${1001 + index}`, 'SYN-CUT', `SYN-C${1001 + index}`),
          exact_primary: false,
          matches: [labelMatch('SYN-CUT')],
        }),
      ),
    },
  },
  {
    page: { truncated: true, max_candidates: 20, omitted: { terms: 1 } },
    warnings: [{ code: 'candidates_truncated', detail: 'SYN-CUT: more than 20 entities match, 20 shown.' }],
  },
)
