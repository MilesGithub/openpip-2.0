import type {
  GraphEdge,
  GraphEdgeEffect,
  GraphEvidenceResponse,
  GraphEvidenceRow,
  GraphFamily,
  GraphLevel,
  GraphNetworkNode,
  GraphNetworkResponse,
  GraphNode,
  GraphOrientation,
  GraphPresentation,
  GraphRelationshipTypeEntry,
  GraphRelease,
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
