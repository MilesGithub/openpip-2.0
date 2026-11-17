import type {
  GraphEdge,
  GraphEdgeEffect,
  GraphEntityResponse,
  GraphEntityStatus,
  GraphEvidencePublication,
  GraphEvidenceResponse,
  GraphEvidenceRow,
  GraphFamily,
  GraphLevel,
  GraphNetworkNode,
  GraphNetworkResponse,
  GraphNode,
  GraphOrientation,
  GraphPresentation,
  GraphPublication,
  GraphRelationshipResponse,
  GraphRelationshipTypeEntry,
  GraphRelease,
  GraphScoreTypeEntry,
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
