import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { GraphBadBodyError } from '../../../api/graph/errors'
import type { GraphErrorInfo } from '../../../api/graph/errors'
import {
  assignedAssociationRow, geneticEvidencePage, geneticRelationshipPage, inconclusiveEdge, inconclusiveRow, labelledPhysicalRow,
  labelledRelease, levelAssignedEdge, makeGraphEdge, makeGraphEvidenceRow, makeGraphRelease, mixedReferenceEdge, mixedSignEdge,
  notSignificantEdge, notSignificantRow, physicalEvidencePage, physicalRelationshipPage, regulationEvidencePage,
  regulationRelationshipPage, regulationRowWithMethods, rowWithoutRecord, signRow, stagedRelease, statedAssociationRow,
  testedAbsentRelease, testScoreTypes, textMiningRow, unknownBasisRow, unknownChannelsEdge, unknownLevelEdge, unspecifiedEdge,
  unspecifiedTestedAbsentEdge,
} from '../../../mocks/fixtures/typedGraph/extras'
import { recorded } from '../../../test/graphHooks'
import type {
  GraphAssertionMethod, GraphEdge, GraphEvidenceBasis, GraphEvidenceResponse, GraphEvidenceRow, GraphLevel, GraphMethodRole,
  GraphNetworkResponse, GraphNode, GraphOutcome, GraphPublication, GraphPublicationRole, GraphRelationshipResponse, GraphRelease,
  GraphResolutionBasis, GraphRowEffect, GraphScoreDirection, GraphScoreValueKind, GraphVocabularies,
} from '../types'
import { EMPTY_GRAPH, graphOfNetwork } from '../network/graph'
import type { TypedGraph } from '../network/graph'
import { ALSO_WORDS, EFFECT_COUNT_WORDS, edgeDescription, slugWords } from '../network/words'
import { evidenceRowView } from './evidence'
import type { EvidenceContext, EvidenceField, EvidenceRowView } from './evidence'
import {
  COUNTING_DEFINITION, COUNTING_LIMITS, DIRECTION_WORDS, METHOD_ROLE_WORDS, PANEL_LABELS, PUBLICATION_ROLE_WORDS, RESOLUTION_WORDS,
  VALUE_KIND_WORDS, contextTermText, contradictionSentences, entityStatusSentence, levelCountsLine, levelLine, moreColumnsText,
  panelErrorFor, panelErrorText, panelMayRetry, tableStatusSentence, tableStatusWord, termText, testedLevelLines,
} from './panelWords'
import type { PanelSubject, RowField } from './panelWords'
import { releaseEntryLines, rowReleaseWords } from './releases'
import { relationshipSummary } from './summary'
import type { RelationshipSummary } from './summary'
import { clipText, isHttpUrl, sourceText } from './text'

// React and the libraries report a bad value through the console instead of throwing (16.0).
beforeEach(() => {
  vi.spyOn(console, 'warn')
  vi.spyOn(console, 'error')
})

afterEach(() => {
  const calls = [...vi.mocked(console.warn).mock.calls, ...vi.mocked(console.error).mock.calls]
  vi.restoreAllMocks()
  expect(calls.map((args) => args.map(String).join(' '))).toEqual([])
})

const ALL_OUTCOMES: GraphOutcome[] = ['supported', 'tested_absent', 'inconclusive', 'not_significant']
const SUBJECTS: PanelSubject[] = ['relationship', 'evidence', 'entity']
const DETAIL = 'DETAIL-MUST-NOT-SHOW'

const vocabularies = () => recorded<GraphVocabularies>('vocabularies').body
/** EL-5's vocabularies: a null model and the not-comparable statement come only from a score type's entry. */
const scoreVocabularies = (): GraphVocabularies => ({ ...vocabularies(), score_types: [...vocabularies().score_types, ...testScoreTypes] })
const network = (name: string): TypedGraph => graphOfNetwork(recorded<GraphNetworkResponse>(name).body)
const page = (name: string) => recorded<GraphRelationshipResponse>(name).body
const evidencePage = (name: string) => recorded<GraphEvidenceResponse>(name).body

function edgeIn(graph: TypedGraph, id: string): GraphEdge {
  const edge = graph.edges.find((candidate) => candidate.id === id)
  if (edge === undefined) throw new Error(`no ${id}`)
  return edge
}

/** A page's graph, built here rather than by graphOfRelationship, so a sentence is not read through the code under test. */
function pageGraph(body: GraphRelationshipResponse, requested: readonly GraphOutcome[]): TypedGraph {
  return { ...EMPTY_GRAPH, nodes: body.nodes, edges: [body.edge], releases: body.releases, requestedOutcomes: requested }
}

function rowContext(edge: GraphEdge | null, releases: readonly GraphRelease[], nodes: readonly GraphNode[], options: {
  requested?: readonly GraphOutcome[]
  vocabularies?: GraphVocabularies | null
} = {}): EvidenceContext {
  return {
    edge,
    releases: new Map(releases.map((release) => [release.id, release] as const)),
    requested: options.requested ?? ['supported'],
    nodes,
    vocabularies: options.vocabularies === undefined ? scoreVocabularies() : options.vocabularies,
  }
}

const regulationContext = (options: { requested?: readonly GraphOutcome[]; vocabularies?: GraphVocabularies | null } = {}) =>
  rowContext(regulationRelationshipPage.edge, regulationEvidencePage.releases, regulationRelationshipPage.nodes, options)
const physicalContext = () => rowContext(physicalRelationshipPage.edge, physicalEvidencePage.releases, physicalRelationshipPage.nodes)
const geneticContext = () => rowContext(geneticRelationshipPage.edge, geneticEvidencePage.releases, geneticRelationshipPage.nodes)

type Line = string | { text: string; href: string }

/** 8.8's fields, written as literals and shaped as the view holds them. */
function fields(...entries: [RowField, string, ...Line[]][]): EvidenceField[] {
  return entries.map(([field, term, ...lines]) => ({
    field,
    term,
    lines: lines.map((line) => (typeof line === 'string' ? { text: line } : line)),
  }))
}

function lineTexts(view: EvidenceRowView, field: RowField): string[] {
  return view.fields.filter((entry) => entry.field === field).flatMap((entry) => entry.lines.map((line) => line.text))
}

/** Every string a summary holds, at any depth, so a word a deploy brings early is checked wherever it lands. */
function stringsOf(value: unknown): string[] {
  if (typeof value === 'string') return [value]
  if (Array.isArray(value)) return value.flatMap(stringsOf)
  if (typeof value === 'object' && value !== null) return Object.values(value).flatMap(stringsOf)
  return []
}

// 8.8's words that several rows share.
const EXTRAS_RELEASE = 'Synthetic extras x1 (synthetic)'
const DEMO_V2 = 'Synthetic regulation example v2 (synthetic)'
const EXPERIMENTAL = 'experimental: rests on an experiment'
const MANUAL = 'manual assertion: a person reviewed the evidence and made the statement'
const AUTOMATIC = "automatic assertion: made by a program without a person's review"
const UNKNOWN_METHOD = 'unknown: not stated'
const NOT_RECORDED = "source's wording: not recorded"
const NO_POLARITY = 'not applicable: this type states no effect'
const NOT_SUPPORTING = 'none: only a supporting result states an effect'
const NOT_STATED = 'not stated by the source'
const NONE_STATED = 'none stated'
const NO_PUBLICATION = 'no publication recorded'
const SCORES_NOTE = 'Each score is read within its own type; scores of different types are never pooled.'
const NOT_COMPARABLE = '; its type says its values are not comparable across datasets'
const REGULATION_ENDS = [
  'SYN-N901 (regulator, as written), resolved to SYN-N901 by its primary identifier',
  'SYN-N902 (target, as written), resolved to SYN-N902 by its primary identifier',
]
const SYMMETRIC_ENDS = [
  'SYN-N901 (participant, as written), resolved to SYN-N901 by its primary identifier',
  'SYN-N902 (participant, as written), resolved to SYN-N902 by its primary identifier',
]

// 8.2's table, copied from the spec: [info, relationship panel, evidence list, entity panel].
const ERROR_WORDS: [GraphErrorInfo, string, string, string][] = [
  [{ kind: 'cancelled' }, 'Loading this relationship was cancelled.', 'Loading the evidence was cancelled.', 'Loading this entity was cancelled.'],
  [
    { kind: 'too_long', bytes: 5000, limit: 4094 },
    'The request for this relationship would be too long to send. Use fewer filters.',
    'The request for this evidence would be too long to send. Use fewer filters.',
    'The request for this entity would be too long to send. Select fewer releases.',
  ],
  [{ kind: 'timeout' }, 'This relationship did not arrive in time.', 'The evidence did not arrive in time.', 'This entity did not arrive in time.'],
  [{ kind: 'network' }, 'The server could not be reached.', 'The server could not be reached.', 'The server could not be reached.'],
  [
    { kind: 'bad_request', status: 400, detail: DETAIL, errors: [] },
    'The server refused the request for this relationship.',
    'The server refused the request for this evidence.',
    'The server refused the request for this entity.',
  ],
  [
    { kind: 'server_refused', status: 414 },
    'The web server refused the request for this relationship, which may be too long. Use fewer filters.',
    'The web server refused the request for this evidence, which may be too long. Use fewer filters.',
    'The web server refused the request for this entity, which may be too long. Select fewer releases.',
  ],
  [
    { kind: 'unauthorized', status: 401 },
    'Your login is no longer valid, so this relationship could not be loaded. Log in again.',
    'Your login is no longer valid, so the evidence could not be loaded. Log in again.',
    'Your login is no longer valid, so this entity could not be loaded. Log in again.',
  ],
  [
    { kind: 'not_found', status: 404, detail: DETAIL },
    'This relationship is not available to you.',
    "This relationship's evidence is not available to you.",
    'This entity is not available to you.',
  ],
  [
    { kind: 'not_acceptable', status: 406 },
    'The server cannot answer in a format this page reads.',
    'The server cannot answer in a format this page reads.',
    'The server cannot answer in a format this page reads.',
  ],
  [
    { kind: 'throttled', status: 429, detail: DETAIL, retryAfter: null },
    "Too many requests: relationships share the network's limit. Try again shortly.",
    'Too many requests. Try again shortly.',
    'Too many requests. Try again shortly.',
  ],
  [
    { kind: 'too_expensive', status: 503, detail: DETAIL },
    'This relationship took too long to compute. Narrow the filters before trying again.',
    'This page of evidence took too long to compute. Narrow the filters before trying again.',
    'This entity could not be loaded now. Try again later.',
  ],
  [
    { kind: 'bad_body', status: 200 },
    "The server's answer could not be read.",
    "The server's answer could not be read.",
    "The server's answer could not be read.",
  ],
  [
    { kind: 'http', status: 500, json: true },
    'This relationship could not be loaded (HTTP 500).',
    'The evidence could not be loaded (HTTP 500).',
    'This entity could not be loaded (HTTP 500).',
  ],
  [{ kind: 'unknown' }, 'This relationship could not be loaded.', 'The evidence could not be loaded.', 'This entity could not be loaded.'],
]

describe('status words', () => {
  it('PW-1: every kind for every subject, never its detail, a claim of absence or the network\'s words', () => {
    expect(ERROR_WORDS.map(([info]) => info.kind)).toHaveLength(14)
    for (const [info, ...words] of ERROR_WORDS) {
      SUBJECTS.forEach((subject, index) => {
        const text = panelErrorText(info, subject)
        expect(text, `${info.kind} ${subject}`).toBe(words[index])
        for (const banned of [DETAIL, 'not enabled', 'does not exist', 'This network', 'undefined', '{']) {
          expect(text, `${info.kind} ${subject}`).not.toContain(banned)
        }
      })
    }
    expect(PANEL_LABELS.tryAgain).toEqual({
      relationship: 'Try loading this relationship again',
      evidence: 'Try loading the evidence again',
      entity: 'Try loading this entity again',
    })
    expect(new Set(Object.values(PANEL_LABELS.tryAgain)).size).toBe(3)
    // panelErrorFor classifies first: a 2xx without a JSON object, and an error axios did not raise.
    expect(panelErrorFor(new GraphBadBodyError(200), 'evidence')).toBe("The server's answer could not be read.")
    expect(panelErrorFor(new Error('x'), 'entity')).toBe('This entity could not be loaded.')
  })

  it('PW-2: a throttled answer with and without Retry-After, and the http kind with its status', () => {
    const throttled = (retryAfter: number | null): GraphErrorInfo => ({ kind: 'throttled', status: 429, detail: null, retryAfter })
    expect(SUBJECTS.map((subject) => panelErrorText(throttled(null), subject))).toEqual([
      "Too many requests: relationships share the network's limit. Try again shortly.",
      'Too many requests. Try again shortly.',
      'Too many requests. Try again shortly.',
    ])
    expect(SUBJECTS.map((subject) => panelErrorText(throttled(1), subject))).toEqual([
      "Too many requests: relationships share the network's limit. Try again in 1 second.",
      'Too many requests. Try again in 1 second.',
      'Too many requests. Try again in 1 second.',
    ])
    expect(SUBJECTS.map((subject) => panelErrorText(throttled(30), subject))).toEqual([
      "Too many requests: relationships share the network's limit. Try again in 30 seconds.",
      'Too many requests. Try again in 30 seconds.',
      'Too many requests. Try again in 30 seconds.',
    ])
    expect(SUBJECTS.map((subject) => panelErrorText({ kind: 'http', status: 501, json: true }, subject))).toEqual([
      'This relationship could not be loaded (HTTP 501).',
      'The evidence could not be loaded (HTTP 501).',
      'This entity could not be loaded (HTTP 501).',
    ])
    expect(SUBJECTS.map((subject) => panelErrorText({ kind: 'http', status: 502, json: false }, subject))).toEqual([
      'This relationship could not be loaded (HTTP 502).',
      'The evidence could not be loaded (HTTP 502).',
      'This entity could not be loaded (HTTP 502).',
    ])
  })

  it('PW-3: "Try again" only where a repeat can change the answer', () => {
    const offered: Record<GraphErrorInfo['kind'], boolean> = {
      cancelled: true,
      too_long: false,
      timeout: true,
      network: true,
      bad_request: false,
      server_refused: false,
      unauthorized: false,
      not_found: false,
      not_acceptable: false,
      throttled: true,
      too_expensive: true,
      bad_body: true,
      http: true,
      unknown: true,
    }
    for (const [info] of ERROR_WORDS) expect(panelMayRetry(info), info.kind).toBe(offered[info.kind])
  })
})

describe('the counts and their limits', () => {
  it('PW-4: the counting definition and its three limits, word for word as 8.4 gives them', () => {
    expect(COUNTING_DEFINITION).toBe(
      'Independent evidence counts observations, not records. Two results describe one observation, and count once, when they have '
      + 'the same outcome and the same origin (the paper that reports them or, when no paper is named as reporting them, the dataset '
      + 'of their release) and their detection methods match or overlap, or either names none; or when they have the same outcome '
      + 'and the source groups them as one observation in one release. Results linked through others count once with them. A record '
      + 'that names two papers as reporting it gives one result per reporting paper, each with that paper as its origin; a paper it '
      + 'only cites, or names for a method, adds no result. A result that a later release of the same dataset restates with the same '
      + 'outcome, the same reporting paper (or none in either release), and matching detection methods counts once, even when the two '
      + 'state different effects.',
    )
    expect(COUNTING_LIMITS).toEqual([
      'Two experiments with the same outcome, reported in one paper, or stated by one dataset when no paper reports them, count once '
      + 'when their records share a detection method or one names none, whatever evidence basis each states.',
      'One experiment whose method two sources name at different levels of detail in PSI-MI counts twice.',
      'openPIP does not yet read the identifiers that sources share for one observation (an IMEx interaction id, for example); once it '
      + 'does, a record that carries one and a record of the same experiment that does not will count twice.',
    ])
    expect(COUNTING_DEFINITION).toContain('gives one result per reporting paper')
    expect(COUNTING_LIMITS[2]).toContain('openPIP does not yet read the identifiers that sources share for one observation')
    expect(PANEL_LABELS.countingLimits).toBe('The count has these stated limits:')
  })
})

describe('entity status', () => {
  it('PW-5: one sentence in the panel, one word or one shared sentence in the table', () => {
    const statuses = ['active', 'provisional', 'merged', 'retired', null, 'syn_status']
    expect(statuses.map(entityStatusSentence)).toEqual([
      'Status: active (a reference release states this identifier).',
      "Status: provisional (created from a release's evidence; no reference release has stated this identifier yet).",
      'Status: merged (merged into another record).',
      'Status: retired (retired, kept for the record).',
      'Status not given.',
      'Status: syn status.',
    ])
    // A slug that names a prototype member is still an unknown slug.
    expect(entityStatusSentence('constructor')).toBe('Status: constructor.')
    expect(statuses.map(tableStatusWord)).toEqual([null, 'provisional', 'merged', 'retired', null, 'syn status'])
    expect(tableStatusSentence(['provisional', 'provisional']))
      .toBe("Every entity in view is provisional: created from a release's evidence; no reference release has stated this identifier yet.")
    expect(tableStatusSentence(['merged'])).toBe('Every entity in view is merged: merged into another record.')
    expect(tableStatusSentence(['syn_status'])).toBe('Every entity in view is syn status.')
    for (const list of [['provisional', 'merged'], ['active'], ['active', 'active'], [null], [null, null], []]) {
      expect(tableStatusSentence(list), JSON.stringify(list)).toBeNull()
    }
  })
})

describe("an evidence row's closed sets", () => {
  it('PW-6: every basis and assertion method from the vocabularies, then the slug; the lookups; termText', () => {
    const bases: [string, string, string][] = [
      ['experimental', 'experimental: rests on an experiment', 'experimental (test label): rests on an experiment'],
      [
        'computational_prediction',
        'computational prediction: rests on a computational prediction, not an experiment',
        'computational prediction (test label): rests on a computational prediction, not an experiment',
      ],
      [
        'computational_inference',
        'computational inference: inferred computationally, not an experiment',
        'computational inference (test label): inferred computationally, not an experiment',
      ],
      ['statistical', 'statistical: rests on a statistical analysis', 'statistical (test label): rests on a statistical analysis'],
      ['text_mining', 'text mining: found by mining text', 'text mining (test label): found by mining text'],
      ['unknown', 'unknown: unknown or not stated', 'unknown (test label): unknown or not stated'],
      // A slug a deploy brings early: its label alone, or its slug's words, with no gloss.
      ['syn_basis', 'syn basis', 'Synthetic test basis'],
      ['constructor', 'constructor', 'constructor'],
    ]
    const methods: [string, string, string][] = [
      ['manual_assertion', MANUAL, 'manual assertion (test label): a person reviewed the evidence and made the statement'],
      ['automatic_assertion', AUTOMATIC, "automatic assertion (test label): made by a program without a person's review"],
      ['unknown', UNKNOWN_METHOD, 'unknown (test label): not stated'],
      ['syn_method', 'syn method', 'Synthetic test method'],
    ]
    const recordedVocab = vocabularies()
    const labelled: GraphVocabularies = {
      ...recordedVocab,
      evidence_bases: [
        ...recordedVocab.evidence_bases.map((entry) => ({ ...entry, label: `${entry.label} (test label)` })),
        { value: 'syn_basis' as string as GraphEvidenceBasis, label: 'Synthetic test basis' },
      ],
      assertion_methods: [
        ...recordedVocab.assertion_methods.map((entry) => ({ ...entry, label: `${entry.label} (test label)` })),
        { value: 'syn_method' as string as GraphAssertionMethod, label: 'Synthetic test method' },
      ],
    }
    const view = (row: GraphEvidenceRow, vocab: GraphVocabularies | null) => evidenceRowView(row, regulationContext({ vocabularies: vocab }))
    for (const [basis, words, labelledWords] of bases) {
      const row = makeGraphEvidenceRow({ evidence_basis: basis as GraphEvidenceBasis })
      expect(lineTexts(view(row, recordedVocab), 'basis'), basis).toEqual([words])
      expect(lineTexts(view(row, null), 'basis'), basis).toEqual([words])
      expect(lineTexts(view(row, labelled), 'basis'), basis).toEqual([labelledWords])
    }
    for (const [method, words, labelledWords] of methods) {
      const row = makeGraphEvidenceRow({ assertion_method: method as GraphAssertionMethod })
      expect(lineTexts(view(row, recordedVocab), 'method'), method).toEqual([words])
      expect(lineTexts(view(row, null), 'method'), method).toEqual([words])
      expect(lineTexts(view(row, labelled), 'method'), method).toEqual([labelledWords])
    }

    expect(RESOLUTION_WORDS).toEqual({
      primary: 'by its primary identifier',
      xref_unique: 'through a cross-reference that only this entity holds',
      alias_unique: 'through a symbol or alias that only this entity holds',
      isoform_entry: 'on its protein entry: the source named an isoform',
      curated: 'by a reviewed resolution file',
    })
    expect(METHOD_ROLE_WORDS).toEqual({
      detection: 'detection method', participant_identification: 'participant identification method', evidence_code: 'evidence code',
    })
    expect(PUBLICATION_ROLE_WORDS).toEqual({ reported_in: 'reported in', method_reference: 'method reference', cited: 'cited' })
    expect(VALUE_KIND_WORDS).toEqual({
      probability: 'a probability', p_value: 'a p-value', q_value: 'a q-value', rank: 'a rank', count: 'a count', continuous: 'a continuous value',
    })
    expect(DIRECTION_WORDS).toEqual({
      higher_is_stronger: 'higher is stronger', lower_is_stronger: 'lower is stronger', signed: 'signed', unordered: 'unordered',
    })

    // Unknown slugs of each lookup, through slugWords: source_local is defined nowhere (U17).
    const unknowns = makeGraphEvidenceRow({
      methods: [{ role: 'syn_role' as string as GraphMethodRole, curie: 'MI:0018', label: 'two hybrid' }],
      scores: [{
        type: 'test:unlisted', label: 'Test unlisted score', value: 1, raw_value: '1',
        value_kind: 'syn_kind' as string as GraphScoreValueKind, direction: 'syn_direction' as string as GraphScoreDirection, unit: '',
      }],
      participants: [
        { side: 'subject', entity: 'entity:901', stated_entity: null, identifier_as_stated: 'SYN-N901', resolution_basis: 'source_local' },
        {
          side: 'object', entity: 'entity:902', stated_entity: null, identifier_as_stated: 'SYN-N902',
          resolution_basis: 'syn_basis' as string as GraphResolutionBasis,
        },
      ],
      publications: [{
        role: 'syn_role' as string as GraphPublicationRole,
        publication: {
          pmid: null, doi: null, pmcid: null, imex_id: null, local_key: 'test-paper', title: 'Test paper', authors: '', journal: '',
          year: null, url: null, is_synthetic: true,
        },
      }],
    })
    const unknownView = evidenceRowView(unknowns, regulationContext())
    expect(lineTexts(unknownView, 'methods')).toEqual(['syn role: two hybrid (MI:0018)'])
    expect(lineTexts(unknownView, 'scores')).toEqual(['Test unlisted score (test:unlisted): 1; syn kind, syn direction', SCORES_NOTE])
    expect(lineTexts(unknownView, 'participants')).toEqual([
      'SYN-N901 (regulator, as written), resolved to SYN-N901 (source local)',
      'SYN-N902 (target, as written), resolved to SYN-N902 (syn basis)',
    ])
    expect(lineTexts(unknownView, 'publications')).toEqual(['syn role: Test paper (synthetic)', 'local key test-paper'])

    expect(termText({ curie: 'MI:0018', label: 'two hybrid' })).toBe('two hybrid (MI:0018)')
    expect(termText({ curie: 'MI:0018', label: null })).toBe('MI:0018')
    expect(termText({ curie: 'MI:0018', label: '' })).toBe('MI:0018')
    expect(contextTermText({ curie: 'MI:0492', label: 'in vitro' })).toBe('in vitro')
    expect(contextTermText({ curie: 'MI:0492', label: null })).toBe('MI:0492')
    expect(contextTermText({ curie: 'MI:0492', label: '' })).toBe('MI:0492')
  })

  it('PW-7: a row\'s effect words for each meaning of a null effect, and the four meanings of "negative" kept apart', () => {
    const effectOf = (row: GraphEvidenceRow, context: EvidenceContext) => lineTexts(evidenceRowView(row, context), 'effect')
    const regulation = regulationContext({ requested: ALL_OUTCOMES })
    const stated: [GraphRowEffect, string][] = [['activating', 'activates'], ['inhibiting', 'inhibits'], ['unknown', 'effect unknown']]
    for (const [effect, words] of stated) expect(effectOf(makeGraphEvidenceRow({ effect }), regulation), effect).toEqual([words])
    expect(effectOf(makeGraphEvidenceRow({ effect: 'syn_effect' as string as GraphRowEffect }), regulation)).toEqual(['syn effect'])

    // A physical type states no effect, whatever the outcome.
    expect(effectOf(assignedAssociationRow, physicalContext())).toEqual([NO_POLARITY])
    const testedAbsentPage = page('relationship-tested-absent-all')
    const testedRow = evidencePage('evidence-tested-absent').evidence[0]
    expect([testedRow.id, testedRow.outcome, testedRow.effect]).toEqual(['evidence:25', 'tested_absent', null])
    const testedContext = rowContext(testedAbsentPage.edge, testedAbsentPage.releases, testedAbsentPage.nodes, { requested: ALL_OUTCOMES })
    expect(testedAbsentPage.edge.type).toBe('transcriptional_regulation')
    expect(effectOf(testedRow, testedContext)).toEqual([NOT_SUPPORTING])
    expect(effectOf(textMiningRow, regulation)).toEqual([NOT_STATED])
    // While no edge is known the type may state no effect, so a supporting row's null says only that none is stated (M5).
    const unknownEdge = rowContext(null, regulationEvidencePage.releases, regulationRelationshipPage.nodes)
    expect(effectOf(textMiningRow, unknownEdge)).toEqual([NONE_STATED])
    expect(effectOf(inconclusiveRow, unknownEdge)).toEqual([NOT_SUPPORTING])

    const meanings = [
      effectOf(testedRow, testedContext)[0],
      effectOf(makeGraphEvidenceRow({ effect: 'inhibiting' }), regulation)[0],
      lineTexts(evidenceRowView(signRow, geneticContext()), 'sign')[0],
      evidenceRowView(notSignificantRow, regulation).title,
    ]
    expect(meanings).toEqual([
      'none: only a supporting result states an effect',
      'inhibits',
      'negative (aggravating)',
      'Not significant at the stated threshold',
    ])
    expect(new Set(meanings).size).toBe(4)
  })
})

describe('effect, sign and level', () => {
  it('PW-8: tested levels, the strongest level and the level counts, joined by semicolons', () => {
    const vocab = vocabularies()
    const all = network('network-all-edges')
    const physicalWithout = (overrides: Partial<GraphEdge>) => makeGraphEdge({
      type: 'physical_interaction', family: 'physical', orientation: 'symmetric', effect: 'not_applicable', ...overrides,
    })
    expect(testedLevelLines(edgeIn(all, 'relationship:4'), vocab)).toEqual(['Tested for direct interaction: tested, not detected'])
    expect(testedLevelLines(physicalWithout({ tested_levels: { tested_absent: ['not_stated'] } }), vocab))
      .toEqual(['Tested for an interaction, level not stated: tested, not detected'])
    expect(testedLevelLines(physicalWithout({ tested_levels: { tested_absent: ['direct_interaction', 'not_stated'] } }), vocab))
      .toEqual(['Tested for direct interaction; an interaction, level not stated: tested, not detected'])
    expect(testedLevelLines(unspecifiedTestedAbsentEdge, vocab)).toEqual(['tested, not detected'])
    expect(testedLevelLines(edgeIn(all, 'relationship:3'), vocab)).toEqual([])
    expect(testedLevelLines(edgeIn(all, 'relationship:7'), vocab)).toEqual([])

    expect(levelLine(edgeIn(all, 'relationship:3'), vocab)).toBe('Strongest level stated by the supporting results in view: direct interaction')
    expect(levelLine(edgeIn(all, 'relationship:4'), vocab))
      .toBe('Strongest level stated by the supporting results in view: physical interaction, level not stated')
    expect(levelLine(levelAssignedEdge, vocab))
      .toBe('Strongest level of the supporting results in view: co-complex association, assigned by openPIP from the stated assay')
    expect(levelLine(unspecifiedEdge, vocab)).toBe(
      'Strongest level stated by the supporting results in view: none: this type states no interaction type or level and claims no physical contact',
    )
    const p2 = edgeIn(network('network-p2-tested-absent'), 'relationship:4')
    expect([p2.level, p2.outcome_counts.supported]).toEqual([null, 1])
    expect(levelLine(p2, vocab))
      .toBe('Strongest level stated by the supporting results in view: not shown, since supporting results are not among the requested outcomes')
    // No supporting result passes the filters at all: then the level is absent, not left out.
    expect(levelLine(physicalWithout({ level: null, outcome_counts: { supported: 0, tested_absent: 1, inconclusive: 0, not_significant: 0 } }), vocab))
      .toBe('Strongest level stated by the supporting results in view: no supporting result in view')
    expect(levelLine(edgeIn(all, 'relationship:9'), vocab)).toBeNull()

    expect(levelCountsLine(edgeIn(all, 'relationship:3'), vocab)).toBe('Supporting results by level: 1 at direct interaction; 1 at physical association')
    expect(levelCountsLine(edgeIn(all, 'relationship:4'), vocab)).toBe('Supporting results by level: 1 at physical interaction, level not stated')
    expect(levelCountsLine(unspecifiedEdge, vocab)).toBeNull()
    expect(levelCountsLine(p2, vocab)).toBeNull()
    expect(levelCountsLine(edgeIn(all, 'relationship:9'), vocab)).toBeNull()
  })

  it('PW-9 (U13 a): the three exports of network/words.ts keep their values', () => {
    expect(EFFECT_COUNT_WORDS).toEqual([
      ['activating', '1 states activation', '{n} state activation'],
      ['inhibiting', '1 states inhibition', '{n} state inhibition'],
      ['unknown', '1 states an unknown effect', '{n} state an unknown effect'],
      ['not_stated', '1 does not state an effect', '{n} do not state an effect'],
    ])
    expect(ALSO_WORDS).toEqual({
      supported: ['also 1 supporting result, not among the requested outcomes', 'also {n} supporting results, not among the requested outcomes'],
      tested_absent: ['also 1 result tested and not detected', 'also {n} results tested and not detected'],
      inconclusive: ['also 1 inconclusive result', 'also {n} inconclusive results'],
      not_significant: ['also 1 result not significant at the stated threshold', 'also {n} results not significant at the stated threshold'],
    })
    expect(slugWords('a_b')).toBe('a b')
  })
})

describe('source text', () => {
  it('PW-10: sourceText, clipText and isHttpUrl', () => {
    const values: [unknown, string][] = [
      ['a', 'a'],
      [1, '1'],
      [true, 'true'],
      [null, 'null'],
      [undefined, ''],
      [{ a: 1 }, '{"a":1}'],
      [['x'], '["x"]'],
      [{ dangerouslySetInnerHTML: { __html: '<b>x</b>' } }, '{"dangerouslySetInnerHTML":{"__html":"<b>x</b>"}}'],
    ]
    for (const [value, text] of values) expect(sourceText(value), String(text)).toBe(text)

    const clipped = clipText('x'.repeat(10_000))
    expect(clipped).toBe(`${'x'.repeat(1000)}… (9000 more characters not shown)`)
    expect(clipped.indexOf('…')).toBe(1000)
    expect(clipText('x'.repeat(1000))).toBe('x'.repeat(1000))
    expect(clipText('x'.repeat(1001))).toBe(`${'x'.repeat(1000)}… (1 more character not shown)`)
    // A pair whose high half is the thousandth unit is kept whole or not at all.
    const astral = clipText(`${'x'.repeat(999)}\u{1F600}${'x'.repeat(100)}`)
    expect(astral).toBe(`${'x'.repeat(999)}… (102 more characters not shown)`)
    const beforeEllipsis = astral.charCodeAt(astral.indexOf('…') - 1)
    expect(beforeEllipsis >= 0xd800 && beforeEllipsis <= 0xdbff).toBe(false)
    expect(clipText('abcdef', 3)).toBe('abc… (3 more characters not shown)')

    for (const address of ['http://example.org', 'https://example.org/paper-1', 'HTTPS://EXAMPLE.ORG']) expect(isHttpUrl(address), address).toBe(true)
    for (const value of ['javascript:alert(1)', 'data:text/html,x', ' https://x', '//x', '', null, 1]) {
      expect(isHttpUrl(value), String(value)).toBe(false)
    }
  })
})

describe('the relationship summary', () => {
  it('PW-11: relationship:3, headline, levels, the by-outcome rows and no reporting paper', () => {
    const physical = page('relationship-physical')
    const summary = relationshipSummary(physical.edge, {
      graph: pageGraph(physical, ['supported']), requested: ['supported'], byRelease: physical.by_release, releases: physical.releases,
      definition: physical.definition, vocabularies: vocabularies(),
    })
    expect(summary).toStrictEqual<RelationshipSummary>({
      typeLabel: 'Physical interaction',
      definition: physical.definition,
      sentence: 'Physical interaction between SYN-P1 and SYN-P2; direct interaction; 0 distinct publications; synthetic demonstration data.',
      notListed: null,
      qualifiers: [],
      contradiction: [],
      neither: null,
      effect: [],
      sign: [],
      level: [
        'Strongest level stated by the supporting results in view: direct interaction',
        'Supporting results by level: 1 at direct interaction; 1 at physical association',
      ],
      headline: ['Supporting results (assertions): 2', 'Independent evidence: 1'],
      byOutcome: [
        { outcome: 'supported', label: 'supporting', results: 2, independent: 1 },
        { outcome: 'tested_absent', label: 'tested, not detected (not among the requested outcomes)', results: 0, independent: 0 },
        { outcome: 'inconclusive', label: 'inconclusive (not among the requested outcomes)', results: 0, independent: 0 },
        {
          outcome: 'not_significant', label: 'not significant at the stated threshold (not among the requested outcomes)', results: 0,
          independent: 0,
        },
      ],
      outside: 'Results outside the current filters: 0',
      publications: 'Publications reporting the supporting results: none named as reporting them',
    })
    // Under level=physical_association one supporting result falls outside the filters (S2).
    const level = page('relationship-physical-level')
    const narrowed = relationshipSummary(level.edge, {
      graph: pageGraph(level, ['supported']), requested: ['supported'], byRelease: level.by_release, releases: level.releases,
      definition: level.definition, vocabularies: vocabularies(),
    })
    expect(narrowed.outside).toBe('Results outside the current filters: 1, by outcome: 1 supporting')
    expect(narrowed.level).toEqual([
      'Strongest level stated by the supporting results in view: physical association',
      'Supporting results by level: 1 at physical association',
    ])
  })

  it('PW-11: relationship:4 under outcome=all, a contradiction from one release with the level tested for', () => {
    const tested = page('relationship-tested-for-all')
    expect(tested.by_release).toEqual([{ release: 'release:2', outcome_counts: { supported: 1, tested_absent: 1, inconclusive: 0, not_significant: 0 } }])
    const summary = relationshipSummary(tested.edge, {
      graph: pageGraph(tested, ALL_OUTCOMES), requested: ALL_OUTCOMES, byRelease: tested.by_release, releases: tested.releases,
      definition: tested.definition, vocabularies: vocabularies(),
    })
    expect(summary).toStrictEqual<RelationshipSummary>({
      typeLabel: 'Physical interaction',
      definition: tested.definition,
      sentence: 'Physical interaction between SYN-P2 and SYN-SHARED; physical interaction, level not stated; '
        + 'also 1 result tested and not detected; 0 distinct publications; synthetic demonstration data.',
      notListed: null,
      qualifiers: [],
      contradiction: ['1 supporting result and 1 result tested for direct interaction and not detected.'],
      neither: null,
      effect: [],
      sign: [],
      level: [
        'Strongest level stated by the supporting results in view: physical interaction, level not stated',
        'Supporting results by level: 1 at physical interaction, level not stated',
        'Tested for direct interaction: tested, not detected',
      ],
      headline: ['Supporting results (assertions): 1', 'Independent evidence: 1'],
      byOutcome: [
        { outcome: 'supported', label: 'supporting', results: 1, independent: 1 },
        { outcome: 'tested_absent', label: 'tested, not detected', results: 1, independent: 1 },
        { outcome: 'inconclusive', label: 'inconclusive', results: 0, independent: 0 },
        { outcome: 'not_significant', label: 'not significant at the stated threshold', results: 0, independent: 0 },
      ],
      outside: 'Results outside the current filters: 0',
      publications: 'Publications reporting the supporting results: none named as reporting them',
    })
  })

  it('PW-11: supporting results that pass the filters but were not requested are never called absent (M3)', () => {
    const p2 = network('network-p2-tested-absent')
    const edge = edgeIn(p2, 'relationship:4')
    const summary = relationshipSummary(edge, {
      graph: p2, requested: ['tested_absent'], byRelease: null, releases: [], definition: '', vocabularies: vocabularies(),
    })
    expect(summary.headline).toEqual([
      'Supporting results (assertions): not among the requested outcomes',
      'Independent evidence: not among the requested outcomes',
    ])
    expect(summary.publications).toBe('Publications reporting the supporting results: not among the requested outcomes')
    expect(summary.level).toEqual([
      'Strongest level stated by the supporting results in view: not shown, since supporting results are not among the requested outcomes',
      'Tested for direct interaction: tested, not detected',
    ])
    expect(summary.byOutcome).toEqual([
      { outcome: 'supported', label: 'supporting (not among the requested outcomes)', results: 1, independent: 1 },
      { outcome: 'tested_absent', label: 'tested, not detected', results: 1, independent: 1 },
      { outcome: 'inconclusive', label: 'inconclusive (not among the requested outcomes)', results: 0, independent: 0 },
      { outcome: 'not_significant', label: 'not significant at the stated threshold (not among the requested outcomes)', results: 0, independent: 0 },
    ])
    const searched = [
      ...summary.headline, ...summary.effect, ...summary.sign, ...summary.level, summary.publications, ...summary.byOutcome.map((row) => row.label),
    ]
    expect(searched.filter((line) => /no support/i.test(line))).toEqual([])
    // The network's sentence, asserted equal and never searched: it still says "no supporting evidence in view" (A72).
    expect(summary.sentence).toBe(edgeDescription(edge, p2, null, vocabularies()))

    // A regulation edge whose supporting result was left out of the request: its effect is not shown, never absent.
    const leftOut = makeGraphEdge({
      effect: null,
      effect_counts: { activating: 0, inhibiting: 0, unknown: 0, not_stated: 0 },
      outcome_counts: { supported: 1, tested_absent: 1, inconclusive: 0, not_significant: 0 },
      independent_outcome_counts: { supported: 1, tested_absent: 1, inconclusive: 0, not_significant: 0 },
      assertion_count: 0,
      evidence_count: 0,
      distinct: { publications: 0, releases: 0, datasets: 0 },
    })
    const regulation = relationshipSummary(leftOut, {
      graph: { ...EMPTY_GRAPH, requestedOutcomes: ['tested_absent'] }, requested: ['tested_absent'], byRelease: null, releases: [],
      definition: '', vocabularies: vocabularies(),
    })
    expect(regulation.effect).toEqual(['Effect: not shown, since supporting results are not among the requested outcomes'])
    const absent = edgeIn(network('network-tf1-outcome-all'), 'relationship:7')
    const none = relationshipSummary(absent, {
      graph: network('network-tf1-outcome-all'), requested: ALL_OUTCOMES, byRelease: null, releases: [], definition: '', vocabularies: vocabularies(),
    })
    expect(none.effect).toEqual(['Effect: no supporting result in view'])
  })

  it('PW-11: a mixed effect from a corrected and a current release, and the release sentences on each side (M26, M59)', () => {
    const both = page('relationship-effect-both-releases')
    const summary = relationshipSummary(both.edge, {
      graph: pageGraph(both, ['supported']), requested: ['supported'], byRelease: both.by_release, releases: both.releases,
      definition: both.definition, vocabularies: vocabularies(),
    })
    expect(summary.effect).toEqual(['Effect: mixed effect', 'Supporting results in view: 1 states activation, 1 states inhibition'])
    expect(summary.contradiction).toEqual([
      'Supporting results state different effects: 1 states activation, 1 states inhibition.',
      'The supporting results come from more than one release.',
      'Synthetic regulation example v1 is among them, and a newer release has since corrected it.',
    ])
    // Two corrected releases are named together, chosen by count.
    const corrected = both.releases.map((release) => ({ ...release, superseded: true }))
    expect(relationshipSummary(both.edge, {
      graph: pageGraph(both, ['supported']), requested: ['supported'], byRelease: both.by_release, releases: corrected, definition: '',
    }).contradiction.slice(2)).toEqual([
      'Synthetic regulation example v1, Synthetic regulation example v2 are among them, and newer releases have since corrected them.',
    ])

    const mixed = page('relationship-mixed')
    expect(relationshipSummary(mixed.edge, {
      graph: pageGraph(mixed, ['supported']), requested: ['supported'], byRelease: mixed.by_release, releases: mixed.releases,
      definition: mixed.definition, vocabularies: vocabularies(),
    }).contradiction).toEqual(['Supporting results state different effects: 1 states activation, 1 states inhibition.'])

    const one = { supported: 1, tested_absent: 0, inconclusive: 0, not_significant: 0 }
    // Two current releases: more than one release, and neither corrected.
    expect(relationshipSummary(mixed.edge, {
      graph: pageGraph(mixed, ['supported']), requested: ['supported'],
      byRelease: [{ release: 'release:901', outcome_counts: one }, { release: 'release:902', outcome_counts: one }],
      releases: [makeGraphRelease(), testedAbsentRelease], definition: '', vocabularies: vocabularies(),
    }).contradiction).toEqual([
      'Supporting results state different effects: 1 states activation, 1 states inhibition.',
      'The supporting results come from more than one release.',
    ])

    const split = [
      { release: 'release:901', outcome_counts: one },
      { release: 'release:902', outcome_counts: { supported: 0, tested_absent: 1, inconclusive: 0, not_significant: 0 } },
    ]
    const referenceGraph = { ...EMPTY_GRAPH, requestedOutcomes: ALL_OUTCOMES, releases: [makeGraphRelease(), testedAbsentRelease] }
    expect(relationshipSummary(mixedReferenceEdge, {
      graph: referenceGraph, requested: ALL_OUTCOMES, byRelease: split, releases: [makeGraphRelease(), testedAbsentRelease], definition: '',
      vocabularies: vocabularies(),
    }).contradiction).toEqual(['1 supporting result and 1 result tested and not detected.', 'They come from different releases.'])
    // Rule 1's entries include the tested-absent one, so its corrected release is named.
    expect(relationshipSummary(mixedReferenceEdge, {
      graph: referenceGraph, requested: ALL_OUTCOMES, byRelease: split,
      releases: [makeGraphRelease(), { ...testedAbsentRelease, superseded: true }], definition: '', vocabularies: vocabularies(),
    }).contradiction).toEqual([
      '1 supporting result and 1 result tested and not detected.',
      'They come from different releases.',
      'Synthetic tested extras x1 is among them, and a newer release has since corrected it.',
    ])
  })

  it('PW-11: a mixed sign, the outcomes neither for nor against, and NOT_LISTED', () => {
    const options = (requested: readonly GraphOutcome[]) => ({
      graph: { ...EMPTY_GRAPH, requestedOutcomes: requested }, requested, byRelease: null, releases: [], definition: '',
      vocabularies: vocabularies(),
    })
    const sign = relationshipSummary(mixedSignEdge, options(['supported']))
    expect(sign.contradiction).toEqual([
      'Supporting results state different genetic signs: 1 states a negative (aggravating) sign, 1 states a positive (alleviating) sign.',
    ])
    expect(sign.sign).toEqual([
      'Genetic sign: mixed sign',
      'Supporting results in view: 1 states a negative (aggravating) sign, 1 states a positive (alleviating) sign',
      'A genetic sign is read relative to the null model of the score that states it; it is not inhibition, and not a result tested and not detected.',
    ])

    for (const edge of [notSignificantEdge, inconclusiveEdge]) {
      const summary = relationshipSummary(edge, options(ALL_OUTCOMES))
      expect(summary.contradiction, edge.id).toEqual([])
      expect(summary.neither, edge.id).toBeNull()
    }
    const threeWays = makeGraphEdge({ outcome_counts: { supported: 1, tested_absent: 0, inconclusive: 1, not_significant: 1 } })
    const neither = relationshipSummary(threeWays, options(ALL_OUTCOMES)).neither
    expect(neither).toBe(
      'Results neither for nor against this relationship: 1 inconclusive result; 1 result not significant at the stated threshold',
    )
    expect(neither).not.toContain('also')

    const hidden = page('relationship-tested-absent')
    expect(hidden.edge.shown).toBe(false)
    expect(relationshipSummary(hidden.edge, {
      ...options(['supported']), graph: pageGraph(hidden, ['supported']), byRelease: hidden.by_release, releases: hidden.releases,
    }).notListed).toBe(
      'Under the current filters a network would not list this relationship: none of its results in view has a requested outcome.',
    )
    expect(relationshipSummary(page('relationship-physical').edge, options(['supported'])).notListed).toBeNull()
  })

  it('PW-11: values a deploy brings early, and an object qualifier, print no "undefined" or "[object"', () => {
    const options = {
      graph: { ...EMPTY_GRAPH, requestedOutcomes: ALL_OUTCOMES }, requested: ALL_OUTCOMES, byRelease: null, releases: [], definition: '',
      vocabularies: vocabularies(),
    }
    for (const edge of [unknownChannelsEdge, unknownLevelEdge]) {
      const strings = stringsOf(relationshipSummary(edge, options))
      expect(strings.filter((text) => text.includes('undefined') || text.includes('[object')), edge.id).toEqual([])
    }
    expect(relationshipSummary(unknownChannelsEdge, options).effect[0]).toBe('Effect: syn effect')
    const qualified = makeGraphEdge({ qualifiers: { site: 'S15', obj: { position: 15 } } })
    expect(relationshipSummary(qualified, options).qualifiers).toEqual(['site: S15', 'obj: {"position":15}'])
  })
})

describe('evidence rows', () => {
  it('PW-12: the regulation rows of 17.3, every field as 8.8 gives it', () => {
    const context = regulationContext()
    expect(evidenceRowView(regulationRowWithMethods, context)).toStrictEqual<EvidenceRowView>({
      id: 'evidence:951',
      title: 'Supporting result',
      outside: false,
      fields: fields(
        ['release', 'Release', EXTRAS_RELEASE],
        ['basis', 'Evidence basis', EXPERIMENTAL],
        ['method', 'Assertion method', MANUAL],
        ['effect', 'Effect', 'activates'],
        ['effectAsStated', 'Effect as stated', "source's wording: up-regulates"],
        ['relationshipAsStated', 'Relationship as stated', 'activates transcription of'],
        ['methods', 'Methods', 'detection method: MI:0402', 'participant identification method: MI:0102', 'evidence code: ECO:0000353'],
        ['eco', 'ECO evidence code', 'ECO:0000353'],
        ['scores', 'Scores', NONE_STATED],
        ['qualifiers', 'Qualifiers', 'assay_note: two replicates'],
        [
          'contexts', 'Contexts', 'tissue: liver (as written: liver)', 'host taxon: synthetic construct (taxid 32630) (as written: 32630)',
          'assay system: MI:0492 (as written: MI:0492)',
        ],
        [
          'participants', 'Participants',
          'SYN-N901-ALIAS (regulator, as written), resolved to SYN-N901 through a symbol or alias that only this entity holds',
          'SYN-N902 (target, as written), resolved to SYN-N902 through a cross-reference that only this entity holds',
        ],
        ['group', 'Evidence group', 'g-1: results the source groups as one observation in this release'],
        [
          'publications', 'Publications',
          'reported in: Hand-written paper 1 (synthetic: not a real publication) (synthetic)',
          'Test A, Test B, Test Journal, 2026',
          'local key extras-paper-1',
          { text: 'https://example.org/paper-1', href: 'https://example.org/paper-1' },
          'method reference: title not recorded (synthetic)',
          'local key extras-method-1',
          'cited: Cited test paper (synthetic)',
          'local key extras-cited-1',
          { text: 'https://example.org/paper-2', href: 'https://example.org/paper-2' },
        ],
      ),
      record: 'extras-01, line 2 (evidence:951)',
      raw: { kind: 'columns', entries: [['record_id', 'extras-01'], ['effect', 'activating'], ['note', 'line one\nline two']], hidden: 0 },
    })
    expect(evidenceRowView(notSignificantRow, context)).toStrictEqual<EvidenceRowView>({
      id: 'evidence:953',
      title: 'Not significant at the stated threshold (not among the requested outcomes)',
      outside: true,
      fields: fields(
        ['release', 'Release', EXTRAS_RELEASE],
        ['basis', 'Evidence basis', 'statistical: rests on a statistical analysis'],
        ['method', 'Assertion method', AUTOMATIC],
        ['effect', 'Effect', NOT_SUPPORTING],
        ['effectAsStated', 'Effect as stated', NOT_RECORDED],
        ['methods', 'Methods', NONE_STATED],
        [
          'scores', 'Scores',
          `Test p-value (test:p_value): <2.2e-16 (as written; no exact number); a p-value, lower is stronger${NOT_COMPARABLE}`,
          `Test effect size (test:effect_size): 0.12 log2; a continuous value, signed; null model: multiplicative${NOT_COMPARABLE}`,
          SCORES_NOTE,
        ],
        [
          'threshold', 'Significance threshold',
          'As the source wrote them: test:effect_size abs>= 0.5; test:p_value < 0.05. A result is significant only when it meets every '
          + 'criterion; this one does not.',
        ],
        ['contexts', 'Contexts', NONE_STATED],
        ['participants', 'Participants', ...REGULATION_ENDS],
        ['publications', 'Publications', NO_PUBLICATION],
      ),
      record: 'extras-953, line 2 (evidence:953)',
      raw: { kind: 'unavailable' },
    })
    expect(evidenceRowView(inconclusiveRow, context)).toStrictEqual<EvidenceRowView>({
      id: 'evidence:954',
      title: 'Inconclusive result (not among the requested outcomes)',
      outside: true,
      fields: fields(
        ['release', 'Release', EXTRAS_RELEASE],
        ['basis', 'Evidence basis', 'computational inference: inferred computationally, not an experiment'],
        ['method', 'Assertion method', UNKNOWN_METHOD],
        ['effect', 'Effect', NOT_SUPPORTING],
        ['effectAsStated', 'Effect as stated', NOT_RECORDED],
        ['methods', 'Methods', NONE_STATED],
        ['scores', 'Scores', NONE_STATED],
        ['contexts', 'Contexts', NONE_STATED],
        ['participants', 'Participants', ...REGULATION_ENDS],
        ['publications', 'Publications', NO_PUBLICATION],
      ),
      record: 'extras-954, line 2 (evidence:954)',
      raw: { kind: 'unavailable' },
    })
    expect(evidenceRowView(textMiningRow, context)).toStrictEqual<EvidenceRowView>({
      id: 'evidence:955',
      title: 'Supporting result',
      outside: false,
      fields: fields(
        ['release', 'Release', EXTRAS_RELEASE],
        ['basis', 'Evidence basis', 'text mining: found by mining text'],
        ['method', 'Assertion method', AUTOMATIC],
        ['effect', 'Effect', NOT_STATED],
        ['effectAsStated', 'Effect as stated', NOT_RECORDED],
        ['methods', 'Methods', NONE_STATED],
        ['scores', 'Scores', NONE_STATED],
        ['contexts', 'Contexts', NONE_STATED],
        [
          'participants', 'Participants', 'SYN-N901 (regulator, as written), resolved to SYN-N901 (source local)',
          'SYN-N902 (target, as written), resolved to SYN-N902 by its primary identifier',
        ],
        ['publications', 'Publications', NO_PUBLICATION],
      ),
      record: 'extras-955, line 2 (evidence:955)',
      raw: { kind: 'unavailable' },
    })
    expect(evidenceRowView(unknownBasisRow, context)).toStrictEqual<EvidenceRowView>({
      id: 'evidence:956',
      title: 'Supporting result',
      outside: false,
      fields: fields(
        ['release', 'Release', EXTRAS_RELEASE],
        ['basis', 'Evidence basis', 'unknown: unknown or not stated'],
        ['method', 'Assertion method', UNKNOWN_METHOD],
        ['effect', 'Effect', 'effect unknown'],
        ['effectAsStated', 'Effect as stated', NOT_RECORDED],
        ['methods', 'Methods', NONE_STATED],
        ['scores', 'Scores', NONE_STATED],
        ['contexts', 'Contexts', NONE_STATED],
        ['participants', 'Participants', ...REGULATION_ENDS],
        ['publications', 'Publications', NO_PUBLICATION],
      ),
      record: 'extras-956, line 2 (evidence:956)',
      raw: { kind: 'unavailable' },
    })
  })

  it('PW-12: the physical and sign rows of 17.3, a level beside a non-supporting outcome under its own term', () => {
    const physical = physicalContext()
    const common = (id: string) => ({ id, title: 'Supporting result', outside: false, record: `extras-${id.slice('evidence:'.length)}, line 2 (${id})` })
    expect(evidenceRowView(labelledPhysicalRow, physical)).toStrictEqual<EvidenceRowView>({
      ...common('evidence:961'),
      fields: fields(
        ['release', 'Release', 'Test labelled release x1'],
        ['basis', 'Evidence basis', EXPERIMENTAL],
        ['method', 'Assertion method', MANUAL],
        ['effect', 'Effect', NO_POLARITY],
        ['effectAsStated', 'Effect as stated', NOT_RECORDED],
        ['interactionType', 'Interaction type', 'physical association (MI:0915)'],
        ['level', 'Interaction level', 'physical association'],
        ['methods', 'Methods', 'detection method: two hybrid (MI:0018)'],
        ['eco', 'ECO evidence code', 'physical interaction evidence used in manual assertion (ECO:0000353)'],
        ['scores', 'Scores', NONE_STATED],
        ['contexts', 'Contexts', NONE_STATED],
        [
          'participants', 'Participants',
          'SYN-N903 (participant, as written), resolved to SYN-N901 on its protein entry: the source named an isoform; the isoform named: entity:903',
          'SYN-N902-CURATED (participant, as written), resolved to SYN-N902 by a reviewed resolution file',
        ],
        ['publications', 'Publications', NO_PUBLICATION],
      ),
      raw: { kind: 'unavailable' },
    })
    expect(evidenceRowView(assignedAssociationRow, physical)).toStrictEqual<EvidenceRowView>({
      ...common('evidence:962'),
      fields: fields(
        ['release', 'Release', EXTRAS_RELEASE],
        ['basis', 'Evidence basis', EXPERIMENTAL],
        ['method', 'Assertion method', MANUAL],
        ['effect', 'Effect', NO_POLARITY],
        ['effectAsStated', 'Effect as stated', NOT_RECORDED],
        ['level', 'Interaction level', 'co-complex association, assigned by openPIP from the stated assay'],
        ['methods', 'Methods', NONE_STATED],
        ['scores', 'Scores', NONE_STATED],
        ['contexts', 'Contexts', NONE_STATED],
        ['participants', 'Participants', ...SYMMETRIC_ENDS],
        ['publications', 'Publications', NO_PUBLICATION],
      ),
      raw: { kind: 'unavailable' },
    })
    expect(evidenceRowView(statedAssociationRow, physical)).toStrictEqual<EvidenceRowView>({
      ...common('evidence:963'),
      fields: fields(
        ['release', 'Release', EXTRAS_RELEASE],
        ['basis', 'Evidence basis', EXPERIMENTAL],
        ['method', 'Assertion method', MANUAL],
        ['effect', 'Effect', NO_POLARITY],
        ['effectAsStated', 'Effect as stated', NOT_RECORDED],
        ['interactionType', 'Interaction type', 'association (MI:0914)'],
        ['level', 'Interaction level', 'association (co-purification)'],
        ['methods', 'Methods', NONE_STATED],
        ['scores', 'Scores', NONE_STATED],
        ['contexts', 'Contexts', NONE_STATED],
        ['participants', 'Participants', ...SYMMETRIC_ENDS],
        ['publications', 'Publications', NO_PUBLICATION],
      ),
      raw: { kind: 'unavailable' },
    })
    expect(evidenceRowView(signRow, geneticContext())).toStrictEqual<EvidenceRowView>({
      ...common('evidence:971'),
      fields: fields(
        ['release', 'Release', EXTRAS_RELEASE],
        ['basis', 'Evidence basis', EXPERIMENTAL],
        ['method', 'Assertion method', MANUAL],
        ['effect', 'Effect', NO_POLARITY],
        ['effectAsStated', 'Effect as stated', NOT_RECORDED],
        ['sign', 'Genetic sign', 'negative (aggravating)'],
        ['methods', 'Methods', NONE_STATED],
        [
          'scores', 'Scores',
          `Test effect size (test:effect_size): -0.4 log2; a continuous value, signed; null model: multiplicative${NOT_COMPARABLE}`,
          SCORES_NOTE,
        ],
        ['contexts', 'Contexts', NONE_STATED],
        ['participants', 'Participants', ...SYMMETRIC_ENDS],
        ['publications', 'Publications', NO_PUBLICATION],
      ),
      raw: { kind: 'unavailable' },
    })

    // relationship:4's tested-absent row: its level is what it was tested for, never a finding (M66).
    const tested = page('relationship-tested-for-all')
    const rows = evidencePage('evidence-tested-for')
    const known = rowContext(tested.edge, rows.releases, tested.nodes, { requested: ALL_OUTCOMES })
    const [supported, testedAbsent] = rows.evidence.map((row) => evidenceRowView(row, known))
    expect([supported.id, testedAbsent.id]).toEqual(['evidence:35', 'evidence:36'])
    expect(testedAbsent.fields.find((entry) => entry.field === 'level')).toEqual({
      field: 'level', term: 'Interaction level tested', lines: [{ text: 'direct interaction' }],
    })
    expect(supported.fields.find((entry) => entry.field === 'level')).toEqual({
      field: 'level', term: 'Interaction level', lines: [{ text: 'physical interaction, level not stated' }],
    })
    expect(lineTexts(testedAbsent, 'effect')).toEqual([NO_POLARITY])
    expect(lineTexts(supported, 'participants')).toEqual([
      'SYN-P3 (participant, as written), resolved to SYN-SHARED by its primary identifier',
      'SYN-P2 (participant, as written), resolved to SYN-P2 by its primary identifier',
    ])
  })

  it("PW-12: evidence-mixed's two rows, their raw columns in the API's order", () => {
    const mixed = page('relationship-mixed')
    const rows = evidencePage('evidence-mixed')
    const context = rowContext(mixed.edge, rows.releases, mixed.nodes)
    const ends = [
      'SYN-TF2 (regulator, as written), resolved to SYN-TF2 by its primary identifier',
      'SYN-G2 (target, as written), resolved to SYN-G2 by its primary identifier',
    ]
    const columns = (effect: string, record: string, paper: string): readonly (readonly [string, string])[] => [
      ['effect', effect], ['outcome', 'supported'], ['directed', ''], ['object_id', 'SYN-G2'], ['record_id', record], ['subject_id', 'SYN-TF2'],
      ['object_type', 'gene'], ['object_label', 'SYN-G2'], ['object_taxon', '32630'], ['publications', `ref:${paper}`],
      ['subject_type', 'gene'], ['subject_label', 'SYN-TF2'], ['subject_taxon', '32630'], ['evidence_basis', 'experimental'],
      ['assertion_method', 'manual_assertion'], ['interaction_type', ''], ['object_namespace', 'synthetic_gene'],
      ['relationship_type', 'transcriptional_regulation'], ['subject_namespace', 'synthetic_gene'], ['score:synthetic:rank', ''],
      ['score:synthetic:confidence', ''],
    ]
    const expected = (id: string, effect: string, paper: number, record: string, line: number): EvidenceRowView => ({
      id,
      title: 'Supporting result',
      outside: false,
      fields: fields(
        ['release', 'Release', DEMO_V2],
        ['basis', 'Evidence basis', EXPERIMENTAL],
        ['method', 'Assertion method', MANUAL],
        ['effect', 'Effect', effect],
        ['effectAsStated', 'Effect as stated', NOT_RECORDED],
        ['methods', 'Methods', NONE_STATED],
        ['scores', 'Scores', NONE_STATED],
        ['contexts', 'Contexts', NONE_STATED],
        ['participants', 'Participants', ...ends],
        [
          'publications', 'Publications', `reported in: SYN demonstration paper ${paper} (synthetic: not a real publication) (synthetic)`, '2026',
          `local key syn-paper-${paper}`,
        ],
      ),
      record: `${record}, line ${line} (${id})`,
      raw: { kind: 'columns', entries: columns(effect === 'activates' ? 'activating' : 'inhibiting', record, `syn-paper-${paper}`), hidden: 0 },
    })
    const [first, second] = rows.evidence.map((row) => evidenceRowView(row, context))
    expect(first).toStrictEqual(expected('evidence:21', 'activates', 1, 'syn-03', 4))
    expect(second).toStrictEqual(expected('evidence:22', 'inhibits', 2, 'syn-04', 5))
    expect(first.record).toBe('syn-03, line 4 (evidence:21)')
  })

  it('PW-12: with no edge known the row words its own type (M5)', () => {
    const rows = evidencePage('evidence-tested-for')
    const context = rowContext(null, rows.releases, [])
    const ends = [
      'SYN-P3 (subject, as written), resolved to entity:13 by its primary identifier',
      'SYN-P2 (object, as written), resolved to entity:12 by its primary identifier',
    ]
    const [supported, tested] = rows.evidence.map((row) => evidenceRowView(row, context))
    expect({ ...supported, raw: null }).toStrictEqual({
      id: 'evidence:35',
      title: 'Supporting result',
      outside: false,
      fields: fields(
        ['release', 'Release', DEMO_V2],
        ['basis', 'Evidence basis', EXPERIMENTAL],
        ['method', 'Assertion method', MANUAL],
        ['effect', 'Effect', NO_POLARITY],
        ['effectAsStated', 'Effect as stated', NOT_RECORDED],
        ['level', 'Interaction level', 'level not stated'],
        ['methods', 'Methods', NONE_STATED],
        ['scores', 'Scores', NONE_STATED],
        ['contexts', 'Contexts', NONE_STATED],
        ['participants', 'Participants', ...ends],
        ['publications', 'Publications', NO_PUBLICATION],
      ),
      record: 'syn-16, line 17 (evidence:35)',
      raw: null,
    })
    expect({ ...tested, raw: null }).toStrictEqual({
      id: 'evidence:36',
      title: 'Tested, not detected (not among the requested outcomes)',
      outside: true,
      fields: fields(
        ['release', 'Release', DEMO_V2],
        ['basis', 'Evidence basis', EXPERIMENTAL],
        ['method', 'Assertion method', MANUAL],
        ['effect', 'Effect', NO_POLARITY],
        ['effectAsStated', 'Effect as stated', NOT_RECORDED],
        ['interactionType', 'Interaction type', 'direct interaction (MI:0407)'],
        ['level', 'Interaction level tested', 'direct interaction'],
        ['methods', 'Methods', NONE_STATED],
        ['scores', 'Scores', NONE_STATED],
        ['contexts', 'Contexts', NONE_STATED],
        ['participants', 'Participants', ...ends],
        ['publications', 'Publications', NO_PUBLICATION],
      ),
      record: 'syn-17, line 18 (evidence:36)',
      raw: null,
    })
    for (const view of [supported, tested]) expect(view.fields.some((entry) => entry.field === 'sign'), view.id).toBe(false)
  })

  it('PW-12: raw views, clipped columns and values, links only for http(s), and the identifier line', () => {
    const context = regulationContext()
    expect(evidenceRowView(makeGraphEvidenceRow(), context).raw).toStrictEqual({ kind: 'unavailable' })
    const withoutRecord = evidenceRowView(rowWithoutRecord, context)
    expect(withoutRecord.raw).toStrictEqual({ kind: 'none' })
    expect(withoutRecord.record).toBe('no stored source record')

    const wide: Record<string, unknown> = {}
    for (let column = 0; column < 201; column += 1) wide[`c${column}`] = column
    const wideView = evidenceRowView(makeGraphEvidenceRow({ record: { key: 'syn-wide', line_number: 1, raw: wide } }), context)
    if (wideView.raw.kind !== 'columns') throw new Error('expected columns')
    expect(wideView.raw.entries).toHaveLength(200)
    expect(wideView.raw.entries[0]).toEqual(['c0', '0'])
    expect(wideView.raw.entries[199]).toEqual(['c199', '199'])
    expect(wideView.raw.hidden).toBe(1)
    expect(moreColumnsText(1)).toBe('1 more column not shown.')
    expect(moreColumnsText(2)).toBe('2 more columns not shown.')

    const long = evidenceRowView(makeGraphEvidenceRow({
      relationship_as_stated: 'x'.repeat(10_000),
      record: { key: 'syn-long', line_number: 1, raw: { ['k'.repeat(1001)]: 'v'.repeat(1001), nested: { a: [1] } } },
    }), context)
    expect(lineTexts(long, 'relationshipAsStated')).toEqual([`${'x'.repeat(1000)}… (9000 more characters not shown)`])
    expect(long.raw).toStrictEqual({
      kind: 'columns',
      entries: [
        [`${'k'.repeat(1000)}… (1 more character not shown)`, `${'v'.repeat(1000)}… (1 more character not shown)`],
        ['nested', '{"a":[1]}'],
      ],
      hidden: 0,
    })

    const paper = (overrides: Partial<GraphPublication>): GraphPublication => ({
      pmid: null, doi: null, pmcid: null, imex_id: null, local_key: null, title: '', authors: '', journal: '', year: null, url: null,
      is_synthetic: true, ...overrides,
    })
    // Values no reader can take for real identifiers, which EXTRAS never holds (U12; M47).
    const identified = evidenceRowView(makeGraphEvidenceRow({
      publications: [
        {
          role: 'reported_in',
          publication: paper({ pmid: 'test-pmid', doi: 'test-doi', pmcid: 'test-pmcid', imex_id: 'test-imex', title: 'Test identifiers paper' }),
        },
        { role: 'cited', publication: paper({ title: 'Test unsafe paper', url: 'javascript:alert(1)' }) },
      ],
    }), context)
    expect(identified.fields.find((entry) => entry.field === 'publications')?.lines).toStrictEqual([
      { text: 'reported in: Test identifiers paper (synthetic)' },
      { text: 'PMID test-pmid; DOI test-doi; PMCID test-pmcid; IMEx test-imex' },
      { text: 'cited: Test unsafe paper (synthetic)' },
      { text: 'javascript:alert(1)' },
    ])

    const views = [regulationRowWithMethods, notSignificantRow, inconclusiveRow, textMiningRow, unknownBasisRow]
      .map((row) => evidenceRowView(row, context))
      .concat([labelledPhysicalRow, assignedAssociationRow, statedAssociationRow].map((row) => evidenceRowView(row, physicalContext())))
      .concat([evidenceRowView(signRow, geneticContext()), identified])
    const links = views.flatMap((view) => view.fields.flatMap((entry) => entry.lines)).filter((line) => line.href !== undefined)
    expect(links).toStrictEqual([
      { text: 'https://example.org/paper-1', href: 'https://example.org/paper-1' },
      { text: 'https://example.org/paper-2', href: 'https://example.org/paper-2' },
    ])
  })
})

describe('releases', () => {
  it('PW-13: each release entry in 8.9\'s order, a link only for an http(s) licence, and the row words', () => {
    const current = page('relationship-mixed').releases[0]
    const corrected = page('relationship-effect-both-releases').releases.find((release) => release.id === 'release:1')
    if (corrected === undefined) throw new Error('no release:1')
    expect([current.id, current.status, corrected.status]).toEqual(['release:2', 'published', 'superseded'])
    // The demonstration's attribution, licence address and note of changes are the recorded release's own.
    const demo = (release: GraphRelease, version: string, status: string) => [
      { term: 'Release', text: `Synthetic regulation example ${version}` },
      { term: 'Status', text: status },
      { term: 'Approved for', text: 'internal' },
      { term: 'Data', text: 'synthetic demonstration data' },
      { term: 'Licence', text: 'MIT License', href: release.licence.url },
      { term: 'Attribution', text: release.attribution },
      { term: "What openPIP's import changes", text: release.changes_note },
    ]
    expect(current.licence.url).toMatch(/^https:\/\//)
    expect(releaseEntryLines(current)).toStrictEqual(demo(current, 'v2', 'published'))
    expect(releaseEntryLines(corrected)).toStrictEqual(demo(corrected, 'v1', 'superseded: a newer release has since corrected it'))
    expect(releaseEntryLines(stagedRelease)).toStrictEqual([
      { term: 'Release', text: 'Synthetic staged extras x2' },
      { term: 'Status', text: 'staged: not yet published' },
      { term: 'Approved for', text: 'internal, public view, public bulk' },
      { term: 'Data', text: 'synthetic demonstration data' },
      { term: 'Licence', text: 'Test licence (CC-BY-4.0)', href: 'https://example.org/licence' },
      { term: 'Attribution', text: 'Test attribution' },
      { term: "What openPIP's import changes", text: 'Hand-written: no import changed it.' },
    ])
    expect(releaseEntryLines(labelledRelease)).toStrictEqual([
      { term: 'Release', text: 'Test labelled release x1' },
      { term: 'Status', text: 'published' },
      { term: 'Approved for', text: 'internal' },
      { term: 'Licence', text: 'no name or SPDX identifier recorded' },
      { term: 'Attribution', text: 'none given' },
      { term: "What openPIP's import changes", text: 'not available: the release names no import adapter that this portal has' },
    ])
    // Hiding an address would drop what the source gave, so it is text where the link would show (M20).
    const unsafeRelease = makeGraphRelease({ licence: { name: 'Test licence', spdx_id: 'MIT', url: 'javascript:alert(1)' } })
    const unsafe = releaseEntryLines(unsafeRelease)
    expect(unsafe.find((line) => line.term === 'Licence')).toStrictEqual({ term: 'Licence', text: 'Test licence (MIT) javascript:alert(1)' })
    expect(unsafe.filter((line) => line.href !== undefined)).toEqual([])
    const spdxOnly = releaseEntryLines(makeGraphRelease({ licence: { name: null, spdx_id: 'CC0-1.0', url: null }, scopes: [] }))
    expect(spdxOnly.find((line) => line.term === 'Licence')).toStrictEqual({ term: 'Licence', text: 'CC0-1.0' })
    expect(spdxOnly.find((line) => line.term === 'Approved for')).toStrictEqual({ term: 'Approved for', text: 'none' })

    expect(rowReleaseWords(current, 'release:2')).toBe('Synthetic regulation example v2 (synthetic)')
    expect(rowReleaseWords(corrected, 'release:1')).toBe('Synthetic regulation example v1 (superseded) (synthetic)')
    expect(rowReleaseWords(stagedRelease, 'release:903')).toBe('Synthetic staged extras x2 (staged: not yet published) (synthetic)')
    expect(rowReleaseWords(labelledRelease, 'release:904')).toBe('Test labelled release x1')
    expect(rowReleaseWords(unsafeRelease, 'release:901')).toBe('Synthetic extras x1 (synthetic)')
    expect(rowReleaseWords(undefined, 'release:999')).toBe('release:999')
  })
})

describe("the contradiction's tested level", () => {
  it('PW-14: a level only the vocabularies name is worded by their label in the contradiction, as in the tested level line, and by its slug without them', () => {
    // A slug D20's four words lack, as a deploy can bring before this code knows it.
    const level = 'syn_level' as string as GraphLevel
    const edge = makeGraphEdge({
      type: 'physical_interaction', family: 'physical', orientation: 'symmetric', effect: 'not_applicable',
      outcome_counts: { supported: 1, tested_absent: 1, inconclusive: 0, not_significant: 0 },
      independent_outcome_counts: { supported: 1, tested_absent: 1, inconclusive: 0, not_significant: 0 },
      tested_levels: { tested_absent: [level] },
    })
    const vocab: GraphVocabularies = {
      ...vocabularies(),
      interaction_levels: [...vocabularies().interaction_levels, { value: level, label: 'synthetic test level', strength: 0 }],
    }
    const labelled = '1 supporting result and 1 result tested for synthetic test level and not detected.'
    expect(testedLevelLines(edge, vocab)).toEqual(['Tested for synthetic test level: tested, not detected'])
    expect(contradictionSentences(edge, null, [], vocab)).toEqual([labelled])
    // The summary passes its own vocabularies, so its note and its tested line name the level alike.
    const summary = relationshipSummary(edge, {
      graph: { ...EMPTY_GRAPH, requestedOutcomes: ALL_OUTCOMES }, requested: ALL_OUTCOMES, byRelease: null, releases: [], definition: '',
      vocabularies: vocab,
    })
    expect(summary.contradiction).toEqual([labelled])
    expect(summary.level).toContain('Tested for synthetic test level: tested, not detected')
    // Without vocabularies both fall back to the slug's words.
    expect(testedLevelLines(edge)).toEqual(['Tested for syn level: tested, not detected'])
    expect(contradictionSentences(edge, null, [])).toEqual(['1 supporting result and 1 result tested for syn level and not detected.'])
    expect(relationshipSummary(edge, {
      graph: { ...EMPTY_GRAPH, requestedOutcomes: ALL_OUTCOMES }, requested: ALL_OUTCOMES, byRelease: null, releases: [], definition: '',
    }).contradiction).toEqual(['1 supporting result and 1 result tested for syn level and not detected.'])
  })
})
