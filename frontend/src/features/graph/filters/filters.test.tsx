import { cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { recorded } from '../../../test/graphHooks'
import * as browserWords from '../../browsers/browserWords'
import { graphOfNetwork } from '../network/graph'
import type { TypedGraph } from '../network/graph'
import { EFFECT_WORDS } from '../network/words'
import type { GraphNetworkResponse, GraphVocabularies } from '../types'
import { EXPLORE_DEFAULT_VIEW, EXPLORE_LIMIT_FALLBACKS, exploreNetworkParams, exploreSeedsView } from '../viewState'
import type { ExploreView } from '../viewState'
import { isolateText } from '../words/isolateText'
import { ExploreToggles, FilterControls, LayerControls } from './FilterControls'
import * as filterWords from './filterWords'
import { layerModel, withLayerFamily, withLayerType, withoutLayerExtra } from './layers'
import {
  EFFECT_ORDER, EXPLORE_FILTERS, EXPLORE_FILTER_IDS, FILTER_GROUP_ORDER, basisOptions, directionOptions, effectOptions, isFiltered,
  methodsOfText, methodsText, releaseNotes, releaseOptions, scoreTypeOptions, taxonOptions, withDirection, withFilterValue, withTaxon,
  withoutFilters,
} from './registry'

// X1 spec 12.2 (XF): pure rows with 12.0 item 2's hooks; the three render rows need no provider (7.8), and are unmounted first.
beforeEach(() => {
  vi.spyOn(console, 'warn')
  vi.spyOn(console, 'error')
})

afterEach(() => {
  cleanup()
  const calls = [...vi.mocked(console.warn).mock.calls, ...vi.mocked(console.error).mock.calls]
  vi.restoreAllMocks()
  expect(calls.map((args) => args.map(String).join(' ').slice(0, 300))).toEqual([])
})

// Words copied from the spec's section 7.9, never from the module under test.
const TOGGLES_NAME = 'Network options'
const FOLLOW_LABEL = 'Follow gene-to-protein links'
const FOLLOW_NOTE = "Adds what reference links join to each chosen entity: a gene's protein entries and their isoforms, an entry's isoforms and genes, and an isoform's entry and that entry's genes."
const OUTCOME_LABEL = 'Show tested-absent results'
const OUTCOME_NOTE = 'Also lists inconclusive and not-significant results: every result that does not support a relationship. Off, the network lists supporting results only.'
const LAYERS_LEGEND = 'Layers'
const LAYERS_LOADING = 'The layer boxes appear here once the list of layers arrives.'
const LAYERS_MINIMUM = 'At least one layer stays on.'
const LAYER_OFF = 'off'
const FAMILY_WORDS = {
  physical: 'Physical interactions', regulation: 'Regulation', rna: 'RNA regulation', genetic: 'Genetic interactions', modification: 'Modifications',
  ligand_receptor: 'Ligands and receptors', association: 'Associations', phenotype: 'Phenotypes', membership: 'Memberships', reference: 'Reference links',
}
const FILTERS_NAME = 'Filters'
const DIRECTION_LABEL = 'Direction from each seed'
const DIRECTION_WORDS = { both: 'Both ways', out: 'Outgoing', in: 'Incoming' }
const DIRECTION_NOTE = 'Outgoing keeps the relationships whose subject is a seed, and incoming those whose object is one; a symmetric relationship, such as a physical interaction, matches either way.'
const EFFECT_LEGEND = 'Effect'
const EFFECT_OPTION_WORDS = { activating: 'activates', inhibiting: 'inhibits', unknown: 'effect unknown', not_stated: 'no effect stated' }
const EFFECT_NOTE = 'Applies only to the supporting results of relationship types that state an effect, such as transcriptional regulation; other results stay.'
const BASIS_LEGEND = 'Evidence basis'
const METHOD_LABEL = 'Detection method'
const METHOD_HINT = 'PSI-MI identifiers, such as MI:0018, separated by commas or spaces; a result matches when it names any one of them.'
const METHOD_APPLY = 'Apply the detection methods'
const SCORE_LEGEND = 'Score types'
const SCORE_NOTE = 'Keeps the results that carry a score of every checked type.'
const TAXON_LABEL = 'Organism of the partners'
const ANY_ORGANISM = 'Any organism'
const TAXON_NOTE = 'Lists only the partners of this organism; an entity the network starts from is never left out for its own organism.'
const RELEASES_LEGEND = 'Releases'
const RELEASES_DEFAULT = 'With no release checked, the view reads every published release you may see.'
const RELEASES_PINNED = 'Only the checked releases are read.'
const RELEASES_WAITING = 'The releases appear here once the network is drawn.'
const RELEASES_UNNAMED = 'This network names no release.'
const CLEAR_FILTERS = 'Clear filters'
const browserScopeText = (slug: string) => `This view reads only the releases of the browser ${slug}; checking a release here reads that release instead.`
const releasesLimitText = (max: number) => (max === 1 ? 'A view reads at most 1 release.' : `A view reads at most ${max} releases.`)
const methodProblemText = (shown: string) => `“${isolateText(shown)}” is not a PSI-MI identifier: write MI: and four digits, such as MI:0018.`
const STYLES = {
  CONTROL_ROW_STYLE: { display: 'flex', flexWrap: 'wrap', gap: 16, alignItems: 'flex-start', margin: '12px 0' },
  GROUP_STYLE: { minWidth: 0, margin: 0, padding: 0, borderWidth: 0 },
  LEGEND_STYLE: { padding: 0, fontWeight: 600 },
  CHECK_LABEL_STYLE: { display: 'block', overflowWrap: 'anywhere' },
  LAYER_TYPES_STYLE: { marginLeft: 24 },
}

const L = EXPLORE_LIMIT_FALLBACKS
const BANNED = /does not exist|not found|undefined|HTTP|No page uses|\b[Nn]one\b/
const vocab = recorded<GraphVocabularies>('vocabularies').body
const body = (name: string) => recorded<GraphNetworkResponse>(name).body
const graph = (name: string): TypedGraph => graphOfNetwork(body(name))
const seeded = exploreSeedsView(['entity:7'])
const V2 = `${isolateText('Synthetic regulation example')} v2 (synthetic)`
const V1 = `${isolateText('Synthetic regulation example')} v1 (superseded) (synthetic)`
const definedKeys = (view: ExploreView) => Object.entries(exploreNetworkParams(view) ?? {}).filter(([, value]) => value !== undefined).map(([key]) => key).sort()
const copyOf = (view: ExploreView) => JSON.parse(JSON.stringify(view)) as ExploreView
const following = (first: Element, second: Element) => (first.compareDocumentPosition(second) & Node.DOCUMENT_POSITION_FOLLOWING) !== 0
const box = (name: string) => screen.getByRole('checkbox', { name }) as HTMLInputElement

describe('the filters', () => {
  it('XF-1: the registry holds the nine filters of 7.6', () => {
    expect(EXPLORE_FILTER_IDS).toEqual(['release', 'type', 'direction', 'effect', 'outcome', 'basis', 'method', 'score_type', 'taxon'])
    expect(EXPLORE_FILTERS).toEqual({
      release: { id: 'release', names: ['releases', 'browser'], wire: ['releases', 'browser'], label: 'Releases', control: 'release_checkboxes', source: 'answer_and_address', change: 'scope', cleared: true },
      type: { id: 'type', names: ['layers'], wire: ['layers'], label: 'Layers', control: 'layer_checkboxes', source: 'vocabularies_relationship_types', change: 'layers', cleared: false },
      direction: { id: 'direction', names: ['direction'], wire: ['direction'], label: 'Direction from each seed', control: 'select', source: 'fixed', change: 'direction', cleared: true },
      effect: { id: 'effect', names: ['effect'], wire: ['effect'], label: 'Effect', control: 'checkboxes', source: 'fixed', change: 'effect', cleared: true },
      outcome: { id: 'outcome', names: ['outcome'], wire: ['outcome'], label: 'Show tested-absent results', control: 'toggle', source: 'fixed', change: 'outcome', cleared: false },
      basis: { id: 'basis', names: ['evidence_basis'], wire: ['evidence_basis'], label: 'Evidence basis', control: 'checkboxes', source: 'vocabularies_evidence_bases', change: 'evidence_basis', cleared: true },
      method: { id: 'method', names: ['method'], wire: ['method'], label: 'Detection method', control: 'curie_box', source: 'typed', change: 'method', cleared: true },
      score_type: { id: 'score_type', names: ['score_types'], wire: ['score'], label: 'Score types', control: 'checkboxes', source: 'vocabularies_score_types', change: 'score_types', cleared: true },
      taxon: { id: 'taxon', names: ['taxon'], wire: ['taxon'], label: 'Organism of the partners', control: 'select', source: 'vocabularies_taxa', change: 'taxon', cleared: true },
    })
    expect(FILTER_GROUP_ORDER).toEqual(['direction', 'effect', 'basis', 'method', 'score_type', 'taxon', 'release'])
    expect(EFFECT_ORDER).toEqual(['activating', 'inhibiting', 'unknown', 'not_stated'])
  })

  const SINGLES: [string, Partial<ExploreView>, string[]][] = [
    ['releases', { releases: ['release:1'] }, ['expand', 'releases', 'seeds']],
    ['browser', { browser: 'synthetic-regulation' }, ['browser', 'expand', 'seeds']],
    ['layers', { layers: ['transcriptional_regulation'] }, ['expand', 'layers', 'seeds']],
    ['direction', { direction: 'out' }, ['direction', 'expand', 'seeds']],
    ['effect', { effect: ['activating'] }, ['effect', 'expand', 'seeds']],
    ['allOutcomes', { allOutcomes: true }, ['expand', 'outcome', 'seeds']],
    ['evidence_basis', { evidence_basis: ['experimental'] }, ['evidence_basis', 'expand', 'seeds']],
    ['method', { method: ['MI:0018'] }, ['expand', 'method', 'seeds']],
    ['score_types', { score_types: ['synthetic:confidence'] }, ['expand', 'score', 'seeds']],
    ['taxon', { taxon: 9606 }, ['expand', 'seeds', 'taxon']],
  ]
  it.each(SINGLES)('XF-2: each filter sends its own parameter and nothing else (%s)', (_name, change, keys) => {
    expect(definedKeys({ ...seeded, ...change })).toEqual(keys)
    expect(definedKeys({ ...seeded, ...change, follow: false })).toEqual(keys.filter((key) => key !== 'expand'))
  })

  it('XF-3: the options come from the vocabularies, the answer and the address', () => {
    expect(directionOptions()).toEqual([{ value: 'both', text: 'Both ways' }, { value: 'out', text: 'Outgoing' }, { value: 'in', text: 'Incoming' }])
    expect(effectOptions(EXPLORE_DEFAULT_VIEW)).toEqual([
      { value: 'activating', text: 'activates', checked: false, off: false, appended: false },
      { value: 'inhibiting', text: 'inhibits', checked: false, off: false, appended: false },
      { value: 'unknown', text: 'effect unknown', checked: false, off: false, appended: false },
      { value: 'not_stated', text: 'no effect stated', checked: false, off: false, appended: false },
    ])
    expect(effectOptions({ ...EXPLORE_DEFAULT_VIEW, effect: ['inhibiting'] }).filter((option) => option.checked).map((option) => option.value)).toEqual(['inhibiting'])
    const bases = [
      ['experimental', 'experimental'], ['computational_prediction', 'computational prediction'], ['computational_inference', 'computational inference'],
      ['statistical', 'statistical'], ['text_mining', 'text mining'], ['unknown', 'unknown'],
    ].map(([value, text]) => ({ value, text, checked: false, off: false, appended: false }))
    expect(basisOptions(EXPLORE_DEFAULT_VIEW, vocab)).toEqual(bases)
    const synBasis = { value: 'syn_basis', text: 'syn basis', checked: true, off: false, appended: true }
    expect(basisOptions({ ...EXPLORE_DEFAULT_VIEW, evidence_basis: ['syn_basis'] }, vocab)).toEqual([...bases, synBasis])
    expect(basisOptions({ ...EXPLORE_DEFAULT_VIEW, evidence_basis: ['syn_basis'] }, null)).toEqual([synBasis])
    const scores = [
      { value: 'synthetic:confidence', text: 'Synthetic confidence', checked: false, off: false, appended: false },
      { value: 'synthetic:rank', text: 'Synthetic rank', checked: false, off: false, appended: false },
    ]
    expect(scoreTypeOptions(EXPLORE_DEFAULT_VIEW, vocab)).toEqual(scores)
    const synScore = { value: 'syn:extra', text: 'syn:extra', checked: true, off: false, appended: true }
    expect(scoreTypeOptions({ ...EXPLORE_DEFAULT_VIEW, score_types: ['syn:extra'] }, vocab)).toEqual([...scores, synScore])
    const taxa = [
      { value: '7227', text: 'Drosophila melanogaster (taxid 7227)' }, { value: '9606', text: 'Homo sapiens (taxid 9606)' },
      { value: '32630', text: 'synthetic construct (taxid 32630)' },
    ]
    expect(taxonOptions(EXPLORE_DEFAULT_VIEW, vocab)).toEqual(taxa)
    expect(taxonOptions({ ...EXPLORE_DEFAULT_VIEW, taxon: 424242 }, vocab)).toEqual([...taxa, { value: '424242', text: 'taxid 424242' }])
    const v2 = { value: 'release:2', text: V2, checked: false, off: false, appended: false }
    expect(releaseOptions(EXPLORE_DEFAULT_VIEW, graph('network-tf1-expand'), L)).toEqual([v2])
    expect(releaseOptions({ ...EXPLORE_DEFAULT_VIEW, releases: ['release:1'] }, graph('network-tf1-expand-v1'), L))
      .toEqual([{ value: 'release:1', text: V1, checked: true, off: false, appended: false }])
    const pinned = { ...EXPLORE_DEFAULT_VIEW, releases: ['release:7'] }
    const seven = { value: 'release:7', text: 'release:7: no relationship in view lists it', checked: true, off: false, appended: true }
    expect(releaseOptions(pinned, graph('network-tf1-expand'), L)).toEqual([v2, seven])
    expect(releaseOptions(pinned, graph('network-tf1-expand'), { ...L, maxReleases: 1 })).toEqual([{ ...v2, off: true }, seven])
    expect(releaseOptions(pinned, null, L)).toEqual([{ value: 'release:7', text: 'release:7', checked: true, off: false, appended: true }])
    const tf1 = body('network-tf1-expand')
    const hostile = graphOfNetwork({ ...tf1, releases: tf1.releases.map((release) => ({ ...release, dataset: { ...release.dataset, title: '\u202E' + 'a'.repeat(300) } })) })
    expect(releaseOptions(EXPLORE_DEFAULT_VIEW, hostile, L).map((option) => option.text))
      .toEqual([`${isolateText(`\u202E${'a'.repeat(199)}… (101 more characters not shown)`)} v2 (synthetic)`])
    // Over a graph null (and with no vocabularies) every other list is the view's values alone.
    expect(scoreTypeOptions({ ...EXPLORE_DEFAULT_VIEW, score_types: ['syn:extra'] }, null)).toEqual([synScore])
    expect(taxonOptions({ ...EXPLORE_DEFAULT_VIEW, taxon: 424242 }, null)).toEqual([{ value: '424242', text: 'taxid 424242' }])
  })

  it('XF-4: the release notes say what the view reads', () => {
    expect(releaseNotes(EXPLORE_DEFAULT_VIEW, null, [], L)).toEqual([RELEASES_DEFAULT, RELEASES_WAITING])
    const pinned = { ...EXPLORE_DEFAULT_VIEW, releases: ['release:7'] }
    expect(releaseNotes(pinned, null, releaseOptions(pinned, null, L), L)).toEqual([RELEASES_PINNED])
    expect(releaseNotes(EXPLORE_DEFAULT_VIEW, graph('network-tf1-expand-visitor'), [], L)).toEqual([RELEASES_DEFAULT, RELEASES_UNNAMED])
    const tf1 = graph('network-tf1-expand')
    const browser = { ...EXPLORE_DEFAULT_VIEW, browser: 'synthetic-regulation' }
    expect(releaseNotes(browser, tf1, releaseOptions(browser, tf1, L), L)[0]).toBe(browserScopeText('synthetic-regulation'))
    const one = { ...EXPLORE_DEFAULT_VIEW, releases: ['release:1'] }
    expect(releaseNotes(one, tf1, releaseOptions(one, tf1, L), L)[0]).toBe(RELEASES_PINNED)
    const cap = { ...L, maxReleases: 1 }
    expect(releaseNotes(one, tf1, releaseOptions(one, tf1, cap), cap)).toEqual([RELEASES_PINNED, 'A view reads at most 1 release.'])
    const fifty = { ...EXPLORE_DEFAULT_VIEW, releases: Array.from({ length: 50 }, (_, index) => `release:${index + 1}`).sort() }
    const notes = releaseNotes(fifty, tf1, releaseOptions(fifty, tf1, L), L)
    expect(notes[notes.length - 1]).toBe('A view reads at most 50 releases.')
  })

  it('XF-5: the writes return new views and refuse what the API would refuse', () => {
    const write = (view: ExploreView, call: (given: ExploreView) => ExploreView) => {
      const before = copyOf(view)
      const result = call(view)
      expect(view).toEqual(before)
      return result
    }
    const inhibiting = write(seeded, (view) => withFilterValue(view, 'effect', 'inhibiting', true))
    expect(inhibiting.effect).toEqual(['inhibiting'])
    const both = write(inhibiting, (view) => withFilterValue(view, 'effect', 'activating', true))
    expect(both.effect).toEqual(['activating', 'inhibiting'])
    const one = write(both, (view) => withFilterValue(view, 'effect', 'inhibiting', false))
    expect(write(one, (view) => withFilterValue(view, 'effect', 'activating', false)).effect).toBeNull()
    expect(write(seeded, (view) => withFilterValue(view, 'effect', 'mixed', true))).toBe(seeded)
    expect(write(seeded, (view) => withFilterValue(view, 'basis', 'experimental', true)).evidence_basis).toEqual(['experimental'])
    expect(write(seeded, (view) => withFilterValue(view, 'score_type', 'synthetic:confidence', true)).score_types).toEqual(['synthetic:confidence'])
    const scoped = write({ ...seeded, browser: 'synthetic-regulation' }, (view) => withFilterValue(view, 'release', 'release:2', true))
    expect([scoped.releases, scoped.browser]).toEqual([['release:2'], null])
    expect(write(scoped, (view) => withFilterValue(view, 'release', 'release:2', false)).releases).toBeNull()
    expect(write(seeded, (view) => withDirection(view, 'out')).direction).toBe('out')
    expect(write({ ...seeded, direction: 'out' }, (view) => withDirection(view, 'both')).direction).toBeNull()
    expect(write({ ...seeded, direction: 'out' }, (view) => withDirection(view, 'x')).direction).toBeNull()
    expect(write(seeded, (view) => withTaxon(view, '9606')).taxon).toBe(9606)
    expect(write({ ...seeded, taxon: 9606 }, (view) => withTaxon(view, '')).taxon).toBeNull()
    expect(write(seeded, (view) => withTaxon(view, 'x'))).toBe(seeded)
    expect(write(seeded, (view) => withTaxon(view, '0'))).toBe(seeded)
    expect(methodsOfText('mi:0018, MI:0018 MI:0407')).toEqual({ methods: ['MI:0018', 'MI:0407'], problem: null })
    expect(methodsOfText('MI:18')).toEqual({ methods: null, problem: 'MI:18' })
    expect(methodsOfText('  ')).toEqual({ methods: [], problem: null })
    expect(methodsOfText('MI:0018,MI:0407')).toEqual({ methods: ['MI:0018', 'MI:0407'], problem: null })
    expect(methodsOfText('mi:0018')).toEqual({ methods: ['MI:0018'], problem: null })
    expect(methodsText(null)).toBe('')
    expect(methodsText(['MI:0018', 'MI:0407'])).toBe('MI:0018, MI:0407')
    expect(isFiltered(EXPLORE_DEFAULT_VIEW)).toBe(false)
    expect(isFiltered({ ...seeded, layers: ['transcriptional_regulation'], follow: false, allOutcomes: true })).toBe(false)
    const eight: Partial<ExploreView>[] = [
      { releases: ['release:1'] }, { browser: 'synthetic-regulation' }, { direction: 'out' }, { effect: ['activating'] },
      { evidence_basis: ['experimental'] }, { method: ['MI:0018'] }, { score_types: ['synthetic:confidence'] }, { taxon: 9606 },
    ]
    for (const field of eight) expect(isFiltered({ ...seeded, ...field }), JSON.stringify(field)).toBe(true)
    const all: ExploreView = { ...seeded, ...Object.assign({}, ...eight), layers: ['transcriptional_regulation'], follow: false, allOutcomes: true }
    expect(write(all, withoutFilters)).toEqual({
      ...all, releases: null, browser: null, direction: null, effect: null, evidence_basis: null, method: null, score_types: null, taxon: null,
    })
  })

  it('XF-6: the layer model counts the drawn edges, holds the last layer and keeps the address\'s extras', () => {
    const tf1 = graph('network-tf1-expand')
    const model = layerModel(EXPLORE_DEFAULT_VIEW, vocab, tf1, false)
    expect(model).toEqual({
      families: [
        {
          family: 'physical', text: 'Physical interactions',
          types: [
            { slug: 'physical_interaction', text: 'Physical interaction', on: true, count: 1, held: false },
            { slug: 'protein_interaction_unspecified', text: 'Protein interaction, type not stated', on: true, count: 0, held: false },
          ],
          state: 'all', count: 1, held: false,
        },
        {
          family: 'regulation', text: 'Regulation',
          types: [{ slug: 'transcriptional_regulation', text: 'Transcriptional regulation', on: true, count: 3, held: false }],
          state: 'all', count: 3, held: false,
        },
      ],
      extras: [], onTypes: 3, held: false,
    })
    const all = graph('network-tf1-expand-outcome-all')
    const regulation = (show: boolean) => layerModel(EXPLORE_DEFAULT_VIEW, vocab, all, show).families.find((family) => family.family === 'regulation')?.count
    expect([regulation(true), regulation(false)]).toEqual([4, 3])
    const empty = layerModel(EXPLORE_DEFAULT_VIEW, vocab, null, false)
    expect(empty.families.flatMap((family) => [family.count, ...family.types.map((type) => type.count)])).toEqual([null, null, null, null, null])
    const physicalOff = withLayerFamily(EXPLORE_DEFAULT_VIEW, model, 'physical', false)
    expect(physicalOff.layers).toEqual(['transcriptional_regulation'])
    const held = layerModel(physicalOff, vocab, tf1, false)
    expect(held.families[0]).toMatchObject({ family: 'physical', state: 'off', count: null })
    expect(held.families[1].held).toBe(true)
    expect(held.families[1].types[0].held).toBe(true)
    expect(held.held).toBe(true)
    expect(withLayerFamily(physicalOff, held, 'regulation', false)).toBe(physicalOff)
    expect(withLayerType(physicalOff, held, 'transcriptional_regulation', false)).toBe(physicalOff)
    const some = withLayerType(EXPLORE_DEFAULT_VIEW, model, 'protein_interaction_unspecified', false)
    expect(some.layers).toEqual(['physical_interaction', 'transcriptional_regulation'])
    const someModel = layerModel(some, vocab, tf1, false)
    expect(someModel.families[0].state).toBe('some')
    expect(withLayerType(some, someModel, 'protein_interaction_unspecified', true).layers).toBeNull()
    const extrasView = { ...EXPLORE_DEFAULT_VIEW, layers: ['encodes', 'family:reference'] }
    const extras = layerModel(extrasView, vocab, tf1, false)
    expect(extras.extras).toEqual([{ piece: 'encodes', text: 'Encodes' }, { piece: 'family:reference', text: 'Reference links' }])
    expect(extras.families.map((family) => family.state)).toEqual(['off', 'off'])
    const lessEncodes = withoutLayerExtra(extrasView, extras, 'encodes')
    expect(lessEncodes.layers).toEqual(['family:reference'])
    expect(withoutLayerExtra(lessEncodes, layerModel(lessEncodes, vocab, tf1, false), 'family:reference').layers).toBeNull()
    const noVocab = layerModel({ ...EXPLORE_DEFAULT_VIEW, layers: ['transcriptional_regulation'] }, null, tf1, false)
    expect(noVocab.families).toEqual([])
    expect(noVocab.extras).toEqual([{ piece: 'transcriptional_regulation', text: 'transcriptional regulation' }])
  })

  it('XF-7: the words of filterWords.ts', () => {
    const constants: [string, string][] = [
      ['TOGGLES_NAME', TOGGLES_NAME], ['FOLLOW_LABEL', FOLLOW_LABEL], ['FOLLOW_NOTE', FOLLOW_NOTE], ['OUTCOME_LABEL', OUTCOME_LABEL],
      ['OUTCOME_NOTE', OUTCOME_NOTE], ['LAYERS_LEGEND', LAYERS_LEGEND], ['LAYERS_LOADING', LAYERS_LOADING], ['LAYERS_MINIMUM', LAYERS_MINIMUM],
      ['LAYER_OFF', LAYER_OFF], ['FILTERS_NAME', FILTERS_NAME], ['DIRECTION_LABEL', DIRECTION_LABEL], ['DIRECTION_NOTE', DIRECTION_NOTE],
      ['EFFECT_LEGEND', EFFECT_LEGEND], ['EFFECT_NOTE', EFFECT_NOTE], ['BASIS_LEGEND', BASIS_LEGEND], ['METHOD_LABEL', METHOD_LABEL],
      ['METHOD_HINT', METHOD_HINT], ['METHOD_APPLY', METHOD_APPLY], ['SCORE_LEGEND', SCORE_LEGEND], ['SCORE_NOTE', SCORE_NOTE],
      ['TAXON_LABEL', TAXON_LABEL], ['ANY_ORGANISM', ANY_ORGANISM], ['TAXON_NOTE', TAXON_NOTE], ['RELEASES_LEGEND', RELEASES_LEGEND],
      ['RELEASES_DEFAULT', RELEASES_DEFAULT], ['RELEASES_PINNED', RELEASES_PINNED], ['RELEASES_WAITING', RELEASES_WAITING],
      ['RELEASES_UNNAMED', RELEASES_UNNAMED], ['CLEAR_FILTERS', CLEAR_FILTERS],
    ]
    const exported: Record<string, unknown> = Object.fromEntries(Object.entries(filterWords))
    expect(constants.map(([name]) => exported[name])).toEqual(constants.map(([, value]) => value))
    expect(filterWords.FAMILY_WORDS).toEqual(FAMILY_WORDS)
    expect(filterWords.DIRECTION_WORDS).toEqual(DIRECTION_WORDS)
    expect(filterWords.EFFECT_OPTION_WORDS).toEqual(EFFECT_OPTION_WORDS)
    expect(filterWords.familyWords('syn_family')).toBe('syn family')
    expect(filterWords.layerText('Regulation', 3, true)).toBe('Regulation: 3 shown')
    expect(filterWords.layerText('Regulation', 1, true)).toBe('Regulation: 1 shown')
    expect(filterWords.layerText('Regulation', 0, true)).toBe('Regulation: 0 shown')
    expect(filterWords.layerText('Regulation', null, true)).toBe('Regulation')
    expect(filterWords.layerText('Regulation', 3, false)).toBe('Regulation: off')
    expect(filterWords.typesGroupName('Physical interactions')).toBe('Physical interactions: types')
    expect(filterWords.methodProblemText('MI:18')).toBe(methodProblemText('MI:18'))
    expect(filterWords.methodProblemText('MI:' + '1'.repeat(1001))).toBe(methodProblemText(`MI:${'1'.repeat(997)}… (4 more characters not shown)`))
    expect(filterWords.browserScopeText('synthetic-regulation')).toBe(
      'This view reads only the releases of the browser synthetic-regulation; checking a release here reads that release instead.',
    )
    expect(filterWords.releasesLimitText(1)).toBe('A view reads at most 1 release.')
    expect(filterWords.releasesLimitText(50)).toBe('A view reads at most 50 releases.')
    expect(filterWords.pinnedRefText('release:7')).toBe('release:7: no relationship in view lists it')
    expect({
      CONTROL_ROW_STYLE: filterWords.CONTROL_ROW_STYLE, GROUP_STYLE: filterWords.GROUP_STYLE, LEGEND_STYLE: filterWords.LEGEND_STYLE,
      CHECK_LABEL_STYLE: filterWords.CHECK_LABEL_STYLE, LAYER_TYPES_STYLE: filterWords.LAYER_TYPES_STYLE,
    }).toEqual(STYLES)
  })

  it('XF-8: no filter word says what may not be said, and the restated words equal F4\'s and F2\'s', () => {
    const strings = Object.entries(filterWords).flatMap(([, value]): string[] => {
      if (typeof value === 'string') return [value]
      if (typeof value === 'object' && value !== null) return Object.values(value).filter((entry): entry is string => typeof entry === 'string')
      return []
    })
    const called = [
      filterWords.familyWords('syn_family'), filterWords.layerText('Regulation', 3, true), filterWords.layerText('Regulation', 1, true),
      filterWords.layerText('Regulation', 0, true), filterWords.layerText('Regulation', null, true), filterWords.layerText('Regulation', 3, false),
      filterWords.typesGroupName('Physical interactions'), filterWords.methodProblemText('MI:18'), filterWords.methodProblemText('MI:' + '1'.repeat(1001)),
      filterWords.browserScopeText('synthetic-regulation'), filterWords.releasesLimitText(1), filterWords.releasesLimitText(50),
      filterWords.pinnedRefText('release:7'),
    ]
    expect(strings.length).toBeGreaterThan(30)
    expect([...strings, ...called].filter((value) => value.includes('not enabled') || BANNED.test(value))).toEqual([])
    expect(filterWords.CLEAR_FILTERS).toBe(browserWords.CLEAR_FILTERS)
    for (const key of ['activating', 'inhibiting', 'unknown'] as const) expect(filterWords.EFFECT_OPTION_WORDS[key]).toBe(EFFECT_WORDS[key])
  })

  it('XF-9: the two toggles: follow checked, tested-absent not, each change reported once', async () => {
    const user = userEvent.setup()
    const onViewChange = vi.fn()
    const { rerender } = render(<ExploreToggles view={EXPLORE_DEFAULT_VIEW} onViewChange={onViewChange} />)
    const group = screen.getByRole('group', { name: TOGGLES_NAME })
    const boxes = within(group).getAllByRole('checkbox')
    expect(boxes).toHaveLength(2)
    expect(boxes[0]).toBe(box(FOLLOW_LABEL))
    expect(boxes[1]).toBe(box(OUTCOME_LABEL))
    expect(boxes[0]).toBeChecked()
    expect(boxes[0]).toHaveAccessibleDescription(FOLLOW_NOTE)
    expect(boxes[1]).not.toBeChecked()
    expect(boxes[1]).toHaveAccessibleDescription(OUTCOME_NOTE)
    expect(group.style.flexWrap).toBe('wrap')
    await user.click(boxes[0])
    expect(onViewChange).toHaveBeenCalledTimes(1)
    expect(onViewChange).toHaveBeenLastCalledWith({ ...EXPLORE_DEFAULT_VIEW, follow: false }, 'follow')
    expect(boxes[0]).toBeChecked()
    await user.click(boxes[1])
    expect(onViewChange).toHaveBeenCalledTimes(2)
    expect(onViewChange).toHaveBeenLastCalledWith({ ...EXPLORE_DEFAULT_VIEW, allOutcomes: true }, 'outcome')
    rerender(<ExploreToggles view={{ ...EXPLORE_DEFAULT_VIEW, follow: false, allOutcomes: true }} onViewChange={onViewChange} />)
    expect(box(FOLLOW_LABEL)).not.toBeChecked()
    expect(box(OUTCOME_LABEL)).toBeChecked()
  })

  it('XF-10: the layer boxes with their counts, the held box and the extras', async () => {
    const user = userEvent.setup()
    const tf1 = graph('network-tf1-expand')
    let focusAtCall: Element | null = null
    const onViewChange = vi.fn(() => {
      focusAtCall = document.activeElement
    })
    const view = EXPLORE_DEFAULT_VIEW
    const props = { vocabularies: vocab, graph: tf1, show: false, onViewChange }
    const { rerender } = render(<LayerControls view={view} {...props} />)
    const layers = screen.getByRole('group', { name: LAYERS_LEGEND })
    const named = ['Physical interactions: 1 shown', 'Physical interaction: 1 shown', 'Protein interaction, type not stated: 0 shown', 'Regulation: 3 shown']
    expect(within(layers).getAllByRole('checkbox').map((node) => node.getAttribute('aria-label') ?? node.closest('label')?.textContent?.trim())).toEqual(named)
    const types = screen.getByRole('group', { name: 'Physical interactions: types' })
    expect(within(types).getAllByRole('checkbox')).toEqual([box('Physical interaction: 1 shown'), box('Protein interaction, type not stated: 0 shown')])
    for (const name of named) expect(box(name)).toBeChecked()
    expect(screen.queryByRole('group', { name: 'Regulation: types' })).toBeNull()
    expect(screen.queryByRole('checkbox', { name: 'Encodes' })).toBeNull()
    expect(screen.queryByRole('checkbox', { name: 'Reference links' })).toBeNull()
    expect(screen.queryByText(LAYERS_MINIMUM)).toBeNull()
    await user.click(box('Physical interactions: 1 shown'))
    expect(onViewChange).toHaveBeenCalledTimes(1)
    expect(onViewChange).toHaveBeenLastCalledWith({ ...view, layers: ['transcriptional_regulation'] }, 'layers')
    rerender(<LayerControls view={{ ...view, layers: ['transcriptional_regulation'] }} {...props} />)
    for (const name of ['Physical interactions: off', 'Physical interaction: off', 'Protein interaction, type not stated: off']) expect(box(name)).not.toBeChecked()
    const regulation = box('Regulation: 3 shown')
    expect(regulation).toBeChecked()
    expect(regulation).toHaveAttribute('aria-disabled', 'true')
    expect(regulation).toBeEnabled()
    expect(regulation).toHaveAccessibleDescription(LAYERS_MINIMUM)
    expect(screen.getByText(LAYERS_MINIMUM)).toBeInTheDocument()
    await user.click(regulation)
    expect(onViewChange).toHaveBeenCalledTimes(1)
    expect(regulation).toBeChecked()
    rerender(<LayerControls view={{ ...view, layers: ['physical_interaction', 'transcriptional_regulation'] }} {...props} />)
    const physical = screen.getByRole('checkbox', { name: /^Physical interactions/ })
    expect(physical).toBePartiallyChecked()
    await user.click(physical)
    expect(onViewChange).toHaveBeenCalledTimes(2)
    expect(onViewChange).toHaveBeenLastCalledWith({ ...view, layers: null }, 'layers')
    expect(screen.getAllByRole('checkbox').filter((node) => node.hasAttribute('aria-describedby'))).toEqual([])
    rerender(<LayerControls view={{ ...view, layers: ['encodes'] }} {...props} />)
    const encodes = box('Encodes')
    expect(encodes).toBeChecked()
    expect(screen.getAllByRole('checkbox').filter((node) => (node as HTMLInputElement).checked)).toEqual([encodes])
    await user.click(encodes)
    expect(onViewChange).toHaveBeenCalledTimes(3)
    expect(onViewChange).toHaveBeenLastCalledWith({ ...view, layers: null }, 'layers')
    expect(focusAtCall).toBe(screen.getByRole('group', { name: LAYERS_LEGEND }))
    rerender(<LayerControls view={{ ...view, layers: null }} {...props} />)
    expect(screen.queryByRole('checkbox', { name: 'Encodes' })).toBeNull()
    expect(document.activeElement).toBe(screen.getByRole('group', { name: LAYERS_LEGEND }))
    rerender(<LayerControls view={{ ...view, layers: null }} {...props} vocabularies={null} />)
    expect(screen.getByText(LAYERS_LOADING)).toBeInTheDocument()
    expect(screen.queryAllByRole('checkbox')).toEqual([])
    expect(screen.getByRole('group', { name: LAYERS_LEGEND }).style.minWidth).toBe('0px')
  })

  it('XF-11: the filters in their order, each change reported once, the method box and Clear filters', async () => {
    const user = userEvent.setup()
    const tf1 = graph('network-tf1-expand')
    const focusAtCall: (Element | null)[] = []
    const onViewChange = vi.fn(() => {
      focusAtCall.push(document.activeElement)
    })
    const D = EXPLORE_DEFAULT_VIEW
    const { rerender } = render(<FilterControls view={D} onViewChange={onViewChange} vocabularies={vocab} graph={null} limits={L} />)
    const filters = screen.getByRole('group', { name: FILTERS_NAME })
    const inside = within(filters)
    const direction = inside.getByRole('combobox', { name: DIRECTION_LABEL }) as HTMLSelectElement
    expect(direction.selectedOptions[0].textContent).toBe('Both ways')
    expect(direction).toHaveAccessibleDescription(DIRECTION_NOTE)
    const effect = inside.getByRole('group', { name: EFFECT_LEGEND })
    expect(within(effect).getByText(EFFECT_NOTE)).toBeInTheDocument()
    expect(within(effect).getAllByRole('checkbox').map((node) => node.closest('label')?.textContent?.trim())).toEqual(['activates', 'inhibits', 'effect unknown', 'no effect stated'])
    const basis = inside.getByRole('group', { name: BASIS_LEGEND })
    expect(within(basis).getAllByRole('checkbox').map((node) => node.closest('label')?.textContent?.trim()))
      .toEqual(['experimental', 'computational prediction', 'computational inference', 'statistical', 'text mining', 'unknown'])
    const method = inside.getByRole('textbox', { name: METHOD_LABEL }) as HTMLInputElement
    expect(method.value).toBe('')
    expect(method).not.toHaveAttribute('aria-invalid')
    expect(method).toHaveAccessibleDescription(METHOD_HINT)
    expect(inside.getByRole('button', { name: METHOD_APPLY })).toBeInTheDocument()
    const score = inside.getByRole('group', { name: SCORE_LEGEND })
    expect(within(score).getByText(SCORE_NOTE)).toBeInTheDocument()
    expect(within(score).getAllByRole('checkbox').map((node) => node.closest('label')?.textContent?.trim())).toEqual(['Synthetic confidence', 'Synthetic rank'])
    const taxon = inside.getByRole('combobox', { name: TAXON_LABEL }) as HTMLSelectElement
    expect(Array.from(taxon.options, (option) => option.textContent))
      .toEqual([ANY_ORGANISM, 'Drosophila melanogaster (taxid 7227)', 'Homo sapiens (taxid 9606)', 'synthetic construct (taxid 32630)'])
    expect(taxon).toHaveAccessibleDescription(TAXON_NOTE)
    const releases = inside.getByRole('group', { name: RELEASES_LEGEND })
    expect(Array.from(releases.querySelectorAll('p'), (node) => node.textContent)).toEqual([RELEASES_DEFAULT, RELEASES_WAITING])
    expect(within(releases).queryAllByRole('checkbox')).toEqual([])
    expect(screen.queryByRole('button', { name: CLEAR_FILTERS })).toBeNull()
    const order = [direction, effect, basis, method, score, taxon, releases]
    order.slice(1).forEach((node, at) => expect(following(order[at], node), String(at + 1)).toBe(true))

    await user.selectOptions(direction, 'Outgoing')
    expect(onViewChange).toHaveBeenCalledTimes(1)
    expect(onViewChange).toHaveBeenLastCalledWith({ ...D, direction: 'out' }, 'direction')
    await user.click(box('inhibits'))
    expect(onViewChange).toHaveBeenCalledTimes(2)
    expect(onViewChange).toHaveBeenLastCalledWith({ ...D, effect: ['inhibiting'] }, 'effect')
    await user.click(box('experimental'))
    expect(onViewChange).toHaveBeenCalledTimes(3)
    expect(onViewChange).toHaveBeenLastCalledWith({ ...D, evidence_basis: ['experimental'] }, 'evidence_basis')
    await user.click(box('Synthetic confidence'))
    expect(onViewChange).toHaveBeenCalledTimes(4)
    expect(onViewChange).toHaveBeenLastCalledWith({ ...D, score_types: ['synthetic:confidence'] }, 'score_types')
    await user.selectOptions(taxon, 'Homo sapiens (taxid 9606)')
    expect(onViewChange).toHaveBeenCalledTimes(5)
    expect(onViewChange).toHaveBeenLastCalledWith({ ...D, taxon: 9606 }, 'taxon')

    rerender(<FilterControls view={D} onViewChange={onViewChange} vocabularies={vocab} graph={tf1} limits={L} />)
    await user.click(box(V2))
    expect(onViewChange).toHaveBeenCalledTimes(6)
    expect(onViewChange).toHaveBeenLastCalledWith({ ...D, releases: ['release:2'] }, 'scope')
    const browser = { ...D, browser: 'synthetic-regulation' }
    rerender(<FilterControls view={browser} onViewChange={onViewChange} vocabularies={vocab} graph={tf1} limits={L} />)
    await user.click(box(V2))
    expect(onViewChange).toHaveBeenCalledTimes(7)
    expect(onViewChange).toHaveBeenLastCalledWith({ ...browser, releases: ['release:2'], browser: null }, 'scope')

    rerender(<FilterControls view={{ ...D, releases: ['release:2'] }} onViewChange={onViewChange} vocabularies={vocab} graph={tf1} limits={L} />)
    expect(box(V2)).toBeChecked()
    await user.click(box(V2))
    expect(onViewChange).toHaveBeenCalledTimes(8)
    expect(onViewChange).toHaveBeenLastCalledWith({ ...D, releases: null }, 'scope')
    expect(focusAtCall[focusAtCall.length - 1]).toBe(screen.getByRole('group', { name: RELEASES_LEGEND }))

    const capped = { ...L, maxReleases: 1 }
    rerender(<FilterControls view={{ ...D, releases: ['release:7'] }} onViewChange={onViewChange} vocabularies={vocab} graph={tf1} limits={capped} />)
    const off = box(V2)
    expect(off).toHaveAttribute('aria-disabled', 'true')
    expect(off).toBeEnabled()
    expect(off).toHaveAccessibleDescription(releasesLimitText(1))
    expect(releasesLimitText(1)).toBe('A view reads at most 1 release.')
    await user.click(off)
    expect(onViewChange).toHaveBeenCalledTimes(8)

    rerender(<FilterControls view={{ ...D, evidence_basis: ['syn_basis'] }} onViewChange={onViewChange} vocabularies={vocab} graph={tf1} limits={L} />)
    const appended = box('syn basis')
    expect(appended).toBeChecked()
    await user.click(appended)
    expect(onViewChange).toHaveBeenCalledTimes(9)
    expect(onViewChange).toHaveBeenLastCalledWith({ ...D, evidence_basis: null }, 'evidence_basis')
    expect(focusAtCall[focusAtCall.length - 1]).toBe(screen.getByRole('group', { name: BASIS_LEGEND }))
    const experimental = box('experimental')
    await user.click(experimental)
    expect(onViewChange).toHaveBeenCalledTimes(10)
    expect(onViewChange).toHaveBeenLastCalledWith({ ...D, evidence_basis: ['experimental', 'syn_basis'] }, 'evidence_basis')
    expect(document.activeElement).toBe(experimental)

    rerender(<FilterControls view={D} onViewChange={onViewChange} vocabularies={vocab} graph={tf1} limits={L} />)
    const methodBox = screen.getByRole('textbox', { name: METHOD_LABEL }) as HTMLInputElement
    await user.type(methodBox, 'mi:0018, MI:0407')
    expect(onViewChange).toHaveBeenCalledTimes(10)
    await user.click(screen.getByRole('button', { name: METHOD_APPLY }))
    expect(onViewChange).toHaveBeenCalledTimes(11)
    expect(onViewChange).toHaveBeenLastCalledWith({ ...D, method: ['MI:0018', 'MI:0407'] }, 'method')
    expect(methodBox.value).toBe('MI:0018, MI:0407')
    await user.clear(methodBox)
    await user.type(methodBox, 'MI:18')
    await user.click(screen.getByRole('button', { name: METHOD_APPLY }))
    expect(onViewChange).toHaveBeenCalledTimes(11)
    const line = within(screen.getByRole('group', { name: FILTERS_NAME })).getByRole('status')
    expect(line.textContent).toBe(methodProblemText('MI:18'))
    expect(methodBox).toHaveAttribute('aria-invalid', 'true')
    expect(methodBox).toHaveAccessibleDescription(`${METHOD_HINT} ${methodProblemText('MI:18')}`)
    rerender(<FilterControls view={{ ...D, method: ['MI:0407'] }} onViewChange={onViewChange} vocabularies={vocab} graph={tf1} limits={L} />)
    expect(methodBox.value).toBe('MI:0407')
    expect(within(screen.getByRole('group', { name: FILTERS_NAME })).getByRole('status').textContent).toBe('')
    expect(methodBox).not.toHaveAttribute('aria-invalid')
    await user.click(screen.getByRole('button', { name: METHOD_APPLY }))
    expect(onViewChange).toHaveBeenCalledTimes(11)

    const filtered: ExploreView = { ...seeded, direction: 'out', effect: ['activating'], layers: ['transcriptional_regulation'], follow: false }
    rerender(<FilterControls view={filtered} onViewChange={onViewChange} vocabularies={vocab} graph={tf1} limits={L} />)
    await user.click(screen.getByRole('button', { name: CLEAR_FILTERS }))
    expect(onViewChange).toHaveBeenCalledTimes(12)
    expect(onViewChange).toHaveBeenLastCalledWith({ ...filtered, direction: null, effect: null }, 'clear')
    expect(focusAtCall[focusAtCall.length - 1]).toBe(screen.getByRole('combobox', { name: DIRECTION_LABEL }))

    rerender(<FilterControls view={{ ...D, direction: 'out' }} onViewChange={onViewChange} vocabularies={vocab} graph={tf1} limits={L} />)
    await user.selectOptions(screen.getByRole('combobox', { name: DIRECTION_LABEL }), 'Both ways')
    expect(onViewChange).toHaveBeenCalledTimes(13)
    expect(onViewChange).toHaveBeenLastCalledWith({ ...D, direction: null }, 'direction')
    rerender(<FilterControls view={{ ...D, taxon: 9606 }} onViewChange={onViewChange} vocabularies={vocab} graph={tf1} limits={L} />)
    await user.selectOptions(screen.getByRole('combobox', { name: TAXON_LABEL }), ANY_ORGANISM)
    expect(onViewChange).toHaveBeenCalledTimes(14)
    expect(onViewChange).toHaveBeenLastCalledWith({ ...D, taxon: null }, 'taxon')
    const group = screen.getByRole('group', { name: FILTERS_NAME })
    expect(group.style.flexWrap).toBe('wrap')
    const fieldsets = Array.from(group.querySelectorAll('fieldset'))
    expect(fieldsets.length).toBeGreaterThan(0)
    for (const fieldset of fieldsets) expect(fieldset.style.minWidth).toBe('0px')
  })
})
