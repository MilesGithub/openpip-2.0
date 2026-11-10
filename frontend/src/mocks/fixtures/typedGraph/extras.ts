import type {
  GraphEvidenceResponse,
  GraphEvidenceRow,
  GraphNode,
  GraphPresentation,
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
