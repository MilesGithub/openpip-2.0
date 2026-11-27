import { cleanup } from '@testing-library/react'
import { AxiosError } from 'axios'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { GraphBadBodyError, GraphRequestTooLongError } from '../../api/graph/errors'
import { browsersWire, normalizeBrowsersParams } from '../../api/graph/params'
import {
  citationCardInstance, externalInstance, hostile, hostileInstance, hostileTypedInstance, instanceItem, makeBrowserResponse,
  makeBrowsersResponse, makeGraphEvidenceRow, makeInstance, papersInstance, plainInstance, plannedInstance, plannedPlainInstance,
  plannedPortalInstance, primaryOutsideInstance, retiredInstance, retiredPortalInstance, syntheticPaperInstance, twoReleaseInstance,
  unavailableInstance, unknownCodesInstance, unlistedInstance,
} from '../../mocks/fixtures/typedGraph/extras'
import { useAuthStore } from '../../store/authStore'
import { recordRequests, recorded } from '../../test/graphHooks'
import type { RequestLog } from '../../test/graphHooks'
import { recordAnswers } from '../../test/graphRoutes'
import * as badgeWords from '../graph/badge/badgeWords'
import { LEGEND_TEXT } from '../graph/legend/legendText'
import * as pageWords from '../graph/pages/pageWords'
import { gateView, routeErrorView } from '../graph/pages/routeState'
import { evidenceRowView } from '../graph/panels/evidence'
import { endName, panelErrorText } from '../graph/panels/panelWords'
import type {
  GraphBrowserResponse, GraphBrowsersResponse, GraphCandidate, GraphInstance, GraphInstanceItem, GraphPublication, GraphResolveResponse,
  GraphVocabularies,
} from '../graph/types'
import { firstCitedText, publicationLines } from '../graph/words/citation'
import { isolateText } from '../graph/words/isolateText'
import { organismTaxids, singleTaxon, taxonName, taxonWords } from '../graph/words/taxa'
import * as workspaceWords from '../graph/workspace/workspaceWords'
import * as browserWords from './browserWords'
import { instanceAction, instanceCardView } from './cardView'
import { galleryFilterOptions } from './filterOptions'
import {
  filterWrite, galleryAddress, isFiltered, isSearched, searchString, withGalleryParam, withoutGalleryParams,
} from './galleryAddress'
import { galleryView } from './galleryView'
import { instanceHeaderView, isInstanceBody } from './headerView'
import { SEED_LIMIT_FALLBACK, galleryBackTo, instanceHref, seedsFromSearch, withSeeds } from './instanceAddress'
import * as seedWords from './seedWords'

let requests: RequestLog
let answers: { answers: string[]; stop: () => void }

beforeEach(() => {
  useAuthStore.getState().logout()
  requests = recordRequests()
  answers = recordAnswers()
  vi.spyOn(console, 'warn')
  vi.spyOn(console, 'error')
})

afterEach(() => {
  cleanup()
  requests.stop()
  answers.stop()
  const calls = [...vi.mocked(console.warn).mock.calls, ...vi.mocked(console.error).mock.calls]
  vi.restoreAllMocks()
  expect(requests.unhandled).toEqual([])
  expect(answers.answers.filter((answer) => answer.startsWith('501 '))).toEqual([])
  expect(calls.map((args) => args.map(String).join(' ').slice(0, 300))).toEqual([])
})

// Words copied from the spec's sections 3.1 to 3.7, never from the modules under test.
const PAGE_LOADING = 'Loading this page…'
const PAGE_UNKNOWN = 'This page could not be loaded.'
const SERVER_UNREACHABLE = 'The server could not be reached.'
const NOT_ENABLED = 'Browsers are not enabled on this portal.'
const LIST_ERROR = 'The browsers could not be loaded.'
const LIST_TIMEOUT = 'The browsers did not arrive in time.'
const EMPTY_LIST = 'No browser is listed here for you.'
const INCOMPLETE_LIST = 'This list may be incomplete: the server sent only its first page.'
const BROWSER_ERROR = 'This browser could not be loaded.'
const BROWSER_NOT_AVAILABLE = 'This browser is not available to you.'
const BROWSER_TIMEOUT = 'This browser did not arrive in time.'
const WHOLE_ADDRESS = 'The gallery cannot use this address.'
const SYNTHETIC_BADGE = 'Synthetic demonstration data'
const ONE_TERM = 'Search for one name or identifier at a time. A name that holds a comma or tab cannot be searched: use an identifier.'
const SEARCHING = 'Searching…'
const SEARCH_FAILED = 'The search could not be run.'
const SEARCH_AGAIN = 'Press Search to try again.'
const SEARCH_TIMEOUT = 'The search did not finish in time.'
const PREFIX_NOTE = "A term that starts with a word and a colon is read as an identifier with its namespace's prefix: to find a name written that way, search for an identifier."
const LIMIT_ONE = 'The network takes at most 1 entity. Remove one to add another.'
const LIMIT_TEN = 'The network takes at most 10 entities. Remove one to add another.'
const FILTER_LABELS = {
  tag: 'Biology area', relationship_type: 'Relationship type', taxon: 'Organism', assay: 'Assay', year: 'Publication year',
  status: 'Stated status', availability: 'Data availability',
}
const ANY_OPTION = {
  tag: 'Any biology area', relationship_type: 'Any relationship type', taxon: 'Any organism', assay: 'Any assay', year: 'Any year',
  status: 'Any stated status', availability: 'Any availability',
}
const PRIMARY = "the study's own release"
const CONTEXT = 'a release that supports it'
const KIND_WORDS = { typed: 'Typed data', portal_ppi: "This portal's search", external: 'External portal', metadata_only: 'Citation card' }
const AVAILABILITY_WORDS = { live: 'Live', planned: 'Planned', external: 'External', metadata_only: 'Data unavailable', retired: 'Retired' }
const STATUS_WORDS = { planned: 'Planned', available: 'Available', retired: 'Retired' }
const AVAILABILITY_OPTION_WORDS = {
  live: 'Live', planned: 'Planned', external: 'External portal', metadata_only: 'Citation card or data unavailable', retired: 'Retired',
}
const VISIBILITY_WORDS = { staff: 'Staff only', unlisted: 'Unlisted' }
const ROLE_WORDS = { primary: PRIMARY, context: CONTEXT }
const TAG_WORDS = {
  protein_interaction: 'Protein interactions', gene_regulation: 'Gene regulation', rna_regulation: 'RNA regulation',
  genetic_interaction: 'Genetic interactions', signaling: 'Signalling', ligand_receptor: 'Ligands and receptors',
  protein_modification: 'Protein modification', disease: 'Disease', host_pathogen: 'Hosts and pathogens', phenotype: 'Phenotypes',
}
const chosenText = (name: string) => `${isolateText(name)} is among the chosen entities.`
const notChosenText = (name: string) => `${isolateText(name)} is not among the chosen entities. Press Search to add it.`
// The rows' own values (VW-3, VW-5, VW-10), as the recorded bodies hold them.
const NO_LICENCE = 'no name or SPDX identifier recorded'
const PAPER_1 = 'SYN demonstration paper 1 (synthetic: not a real publication)'
const PAPER_2 = 'SYN demonstration paper 2 (synthetic: not a real publication)'
const MIT = 'https://github.com/Helmy-Lab/openpip-2.0/blob/main/LICENSE'
const TF1 = 'SYN-TF1 (Gene)'
// A source value past clipText's 1,000 characters, and F3's cut of it (2.4; AD-6's form).
const LONG = 'a'.repeat(5000)
const CLIPPED = `${'a'.repeat(1000)}… (4000 more characters not shown)`

const vocabularies = recorded<GraphVocabularies>('vocabularies').body
const galleryBody = () => recorded<GraphBrowsersResponse>('browsers').body
const typedBody = () => recorded<GraphBrowserResponse>('browser-typed').body
const resolveBody = (name: string) => recorded<GraphResolveResponse>(name).body

function answered(status: number, data: unknown, headers: Record<string, string> = {}): AxiosError {
  return new AxiosError('x', undefined, undefined, undefined, { status, statusText: '', headers, config: {} as never, data })
}
const networkFailure = () => new AxiosError('x', 'ERR_NETWORK')
const timedOut = () => new AxiosError('x', 'ECONNABORTED')
const params = (text: string) => new URLSearchParams(text)
const address = (text: string) => galleryAddress(params(text))
const query = (data: unknown, error: unknown = null, isPlaceholderData = false) => ({ data, error, isPlaceholderData })
const withInstances = (n: number, next: string | null = null) => ({ ...galleryBody(), instances: galleryBody().instances.slice(0, n), page: { next } })
const values = (list: readonly { value: string }[]) => list.map((option) => option.value)
const last = <T>(list: readonly T[]) => list[list.length - 1]
// A term's code points, since a control character prints as nothing in an assertion's message.
const codePoints = (text: string) => Array.from(text, (character) => `U+${(character.codePointAt(0) ?? 0).toString(16).toUpperCase().padStart(4, '0')}`).join(' ')

describe('the address codecs (12.1: v1 addresses.test.ts, merged)', () => {
  it('AD-1: a full address gives the params, keeps the raw values for the controls and never sends an unknown name', () => {
    const read = address('q=%20search%20&tag=gene_regulation&relationship_type=transcriptional_regulation&taxon=32630&assay=MI:0018&year=2026&status=planned&availability=metadata_only&utm_source=news')
    expect(read.params).toEqual({
      q: 'search', tag: 'gene_regulation', relationship_type: 'transcriptional_regulation', taxon: 32630, assay: 'MI:0018', year: 2026,
      status: 'planned', availability: 'metadata_only',
    })
    expect(read.invalid).toEqual([])
    expect(read.shown).toEqual({
      q: ' search ', tag: 'gene_regulation', relationship_type: 'transcriptional_regulation', taxon: '32630', assay: 'MI:0018', year: '2026',
      status: 'planned', availability: 'metadata_only',
    })
    expect(Object.keys(browsersWire(normalizeBrowsersParams(read.params)))).not.toContain('utm_source')
  })

  it('AD-2: each malformed or repeated value is invalid, by name, in parameter order', () => {
    const invalid = (search: URLSearchParams | string) => galleryAddress(typeof search === 'string' ? params(search) : search).invalid
    expect(invalid('q=')).toEqual(['q'])
    expect(invalid('q=%20%20')).toEqual(['q'])
    expect(invalid(`q=${'a'.repeat(201)}`)).toEqual(['q'])
    expect(invalid(`q=${'a'.repeat(200)}`)).toEqual([])
    // The cap counts the trimmed value, by code point.
    expect(invalid(new URLSearchParams({ q: ` ${'a'.repeat(200)} ` }))).toEqual([])
    const emoji = '\u{1F600}'.repeat(200)
    expect(emoji.length).toBe(400)
    expect(invalid(new URLSearchParams({ q: emoji }))).toEqual([])
    expect(invalid('q=a%09b')).toEqual(['q'])
    expect(invalid('q=a%C2%85b')).toEqual(['q'])
    for (const taxon of ['0', '01', '-1', '1e3', 'abc', '', '99999999999999999999']) expect(invalid(`taxon=${taxon}`), taxon).toEqual(['taxon'])
    expect(invalid('year=32768')).toEqual(['year'])
    expect(invalid('year=32767')).toEqual([])
    expect(invalid('tag=')).toEqual(['tag'])
    expect(invalid(`tag=${'a'.repeat(201)}`)).toEqual(['tag'])
    expect(invalid(`tag=${'a'.repeat(200)}`)).toEqual([])
    expect(invalid('assay=MI%3A0018%00')).toEqual(['assay'])
    const status = address('status=foo')
    expect(status.invalid).toEqual([])
    expect(status.params.status).toBe('foo')
    const twice = address('tag=a&tag=b')
    expect(twice.invalid).toEqual(['tag'])
    expect(twice.shown.tag).toBe('a')
    expect(invalid('year=x&q=')).toEqual(['q', 'year'])
  })

  it('AD-3: the writes, the search string, the two predicates and the filter write', () => {
    expect(withGalleryParam(params('tag=a&utm_source=news'), 'tag', 'b').toString()).toBe('tag=b&utm_source=news')
    expect(withGalleryParam(params('tag=a&tag=c&utm_source=news'), 'tag', 'b').getAll('tag')).toEqual(['b'])
    expect(withGalleryParam(params('tag=a&utm_source=news'), 'tag', '').toString()).toBe('utm_source=news')
    expect(withGalleryParam(params('utm_source=news'), 'year', '2026').toString()).toBe('utm_source=news&year=2026')
    // A write touches its own name only: the search and the other filters stay.
    expect(withGalleryParam(params('q=x&tag=a&utm_source=news'), 'year', '2026').toString()).toBe('q=x&tag=a&utm_source=news&year=2026')
    expect(withGalleryParam(params('q=x&tag=a&utm_source=news'), 'q', 'y').toString()).toBe('q=y&tag=a&utm_source=news')
    expect(withoutGalleryParams(params('q=x&tag=a&year=1&utm_source=news'), ['tag', 'year']).toString()).toBe('q=x&utm_source=news')
    // Each write copies: the router's own params are never changed in place.
    const given = params('tag=a&utm_source=news')
    withGalleryParam(given, 'tag', 'b')
    withoutGalleryParams(given, ['tag'])
    expect(given.toString()).toBe('tag=a&utm_source=news')
    expect(searchString(params(''))).toBe('')
    expect(searchString(params('tag=gene_regulation'))).toBe('?tag=gene_regulation')
    expect([isSearched(address('q=a')), isFiltered(address('q=a'))]).toEqual([true, false])
    expect([isSearched(address('tag=a')), isFiltered(address('tag=a'))]).toEqual([false, true])
    expect([isSearched(address('q=a&year=2026')), isFiltered(address('q=a&year=2026'))]).toEqual([true, true])
    expect([isSearched(address('')), isFiltered(address(''))]).toEqual([false, false])
    expect(filterWrite(null, 'tag')).toEqual({ state: { filter: 'tag' }, replace: false })
    expect(filterWrite({ filter: 'tag' }, 'tag')).toEqual({ state: { filter: 'tag' }, replace: true })
    expect(filterWrite({ filter: 'year' }, 'tag').replace).toBe(false)
    expect(filterWrite('tag', 'tag').replace).toBe(false)
    expect(filterWrite(undefined, 'tag').replace).toBe(false)
  })

  it('AD-4: the seeds codec takes refs of the API grammar, distinct, at most the limit', () => {
    const read = (text: string) => seedsFromSearch(params(text), 10)
    const refs = (n: number) => Array.from({ length: n }, (_, index) => `entity:${index + 1}`).join(',')
    expect(read('')).toEqual({ seeds: [], invalid: false })
    expect(read('seeds=entity:7')).toEqual({ seeds: ['entity:7'], invalid: false })
    expect(read('seeds=entity%3A7%2Centity%3A13')).toEqual({ seeds: ['entity:7', 'entity:13'], invalid: false })
    expect(read(`seeds=${refs(10)}`)).toEqual({ seeds: refs(10).split(','), invalid: false })
    expect(read(`seeds=entity:${'9'.repeat(19)}`)).toEqual({ seeds: [`entity:${'9'.repeat(19)}`], invalid: false })
    for (const text of ['seeds=', 'seeds=entity:0', 'seeds=entity:07', 'seeds=relationship:3', 'seeds=entity:7,entity:7', 'seeds=entity:7,',
      'seeds=entity:7&seeds=entity:8', `seeds=${refs(11)}`, `seeds=entity:${'9'.repeat(20)}`]) {
      expect(read(text), text).toEqual({ seeds: [], invalid: true })
    }
    // The limit is the caller's, the page's limit from status: two refs are too many for a limit of 1, one is not.
    expect(seedsFromSearch(params('seeds=entity:7,entity:13'), 1)).toEqual({ seeds: [], invalid: true })
    expect(seedsFromSearch(params('seeds=entity:7'), 1)).toEqual({ seeds: ['entity:7'], invalid: false })
    // The page's fallback, for a status with no usable limit, is the API's MAX_SEEDS: ten refs pass and eleven do not.
    expect(seedsFromSearch(params(`seeds=${refs(10)}`), SEED_LIMIT_FALLBACK).invalid).toBe(false)
    expect(seedsFromSearch(params(`seeds=${refs(11)}`), SEED_LIMIT_FALLBACK).invalid).toBe(true)
  })

  it('AD-5: withSeeds round-trips, the instance links and the way back', () => {
    const written = withSeeds(params('utm_source=news'), ['entity:7', 'entity:13'])
    expect(seedsFromSearch(written, 10)).toEqual({ seeds: ['entity:7', 'entity:13'], invalid: false })
    expect(written.get('utm_source')).toBe('news')
    expect(withSeeds(params('seeds=entity:7&utm_source=news'), []).toString()).toBe('utm_source=news')
    expect(instanceHref('synthetic-regulation')).toBe('/browsers/synthetic-regulation')
    expect(instanceHref('synthetic-regulation', ['entity:7'])).toBe('/browsers/synthetic-regulation?seeds=entity%3A7')
    expect(instanceHref('a/b')).toBe('/browsers/a%2Fb')
    expect(galleryBackTo({ gallerySearch: '?tag=x' })).toEqual({ pathname: '/browsers', search: '?tag=x' })
    for (const state of [{ gallerySearch: 'tag=x' }, { gallerySearch: 3 }, null, undefined, '?tag=x']) {
      expect(galleryBackTo(state), String(state)).toEqual({ pathname: '/browsers', search: '' })
    }
  })

  it('AD-6: the filter options are the union over the unfiltered list, with the address values appended and every text clipped', () => {
    const options = galleryFilterOptions(recorded('browsers').body, vocabularies, {})
    expect(options.tag).toEqual([{ value: 'gene_regulation', text: 'Gene regulation' }, { value: 'protein_interaction', text: 'Protein interactions' }])
    expect(options.relationship_type).toEqual([
      { value: 'physical_interaction', text: 'Physical interaction' }, { value: 'transcriptional_regulation', text: 'Transcriptional regulation' },
    ])
    expect(options.taxon).toEqual([{ value: '32630', text: 'synthetic construct (taxid 32630)' }])
    expect(options.assay).toEqual([])
    expect(options.year).toEqual([{ value: '2026', text: '2026' }])
    expect(options.status).toEqual([{ value: 'available', text: 'Available' }])
    expect(options.availability).toEqual([{ value: 'live', text: 'Live' }])
    const shown = galleryFilterOptions(recorded('browsers').body, vocabularies, {
      tag: 'disease', taxon: '9606', assay: 'MI:0018', year: '1999', status: 'planned', availability: 'metadata_only',
    })
    expect([last(shown.tag), last(shown.taxon), last(shown.assay), last(shown.year), last(shown.status), last(shown.availability)]).toEqual([
      { value: 'disease', text: 'Disease' }, { value: '9606', text: 'Homo sapiens (taxid 9606)' }, { value: 'MI:0018', text: 'MI:0018' },
      { value: '1999', text: '1999' }, { value: 'planned', text: 'Planned' }, { value: 'metadata_only', text: 'Citation card or data unavailable' },
    ])
    // Only a non-empty value the list lacks is appended, and a taxon that is no taxid reads as given.
    const kept = galleryFilterOptions(recorded('browsers').body, vocabularies, { tag: 'gene_regulation', year: '2026', status: '', taxon: 'abc' })
    expect(kept.tag).toEqual([{ value: 'gene_regulation', text: 'Gene regulation' }, { value: 'protein_interaction', text: 'Protein interactions' }])
    expect(kept.year).toEqual([{ value: '2026', text: '2026' }])
    expect(kept.status).toEqual([{ value: 'available', text: 'Available' }])
    expect(kept.taxon).toEqual([{ value: '32630', text: 'synthetic construct (taxid 32630)' }, { value: 'abc', text: 'abc' }])
    const long = galleryFilterOptions(recorded('browsers').body, vocabularies, { tag: 'a'.repeat(5000) })
    expect(last(long.tag).text).toBe(`${'a'.repeat(1000)}… (4000 more characters not shown)`)
    const made = galleryFilterOptions(makeBrowsersResponse([papersInstance, primaryOutsideInstance, citationCardInstance]), vocabularies, {})
    expect(made.year).toEqual([{ value: '2026', text: '2026' }, { value: '2019', text: '2019' }])
    expect(values(made.taxon)).toEqual(['9606', '32630'])
    // The 2019 paper seen first: the years are sorted, not kept in the order met.
    expect(values(galleryFilterOptions(makeBrowsersResponse([primaryOutsideInstance, papersInstance]), vocabularies, {}).year)).toEqual(['2026', '2019'])
    // Values met out of order come back in value order, for the four filters that the recorded list cannot order.
    const order = galleryFilterOptions(makeBrowsersResponse([retiredInstance, plannedInstance, makeInstance({
      slug: 'syn-order', relationship_types: ['transcriptional_regulation', 'physical_interaction'],
      assays: [{ curie: 'MI:0407', label: 'SYN b' }, { curie: 'MI:0018', label: 'SYN a' }],
    })]), vocabularies, {})
    expect([values(order.relationship_type), values(order.assay), values(order.status), values(order.availability)]).toEqual([
      ['physical_interaction', 'transcriptional_regulation'], ['MI:0018', 'MI:0407'], ['available', 'planned', 'retired'], ['live', 'planned', 'retired'],
    ])
    const visitor = galleryFilterOptions(recorded('browsers-visitor').body, vocabularies, {})
    expect(Object.values(visitor).every((list) => list.length === 0)).toBe(true)
  })
})

describe('the view modules (12.2)', () => {
  const PAIRS: [string, string, string][] = [
    ['live', 'typed', 'Typed data · Live'],
    ['live', 'portal_ppi', "This portal's search"],
    ['external', 'external', 'External portal'],
    ['metadata_only', 'typed', 'Typed data · Data unavailable'],
    ['metadata_only', 'metadata_only', 'Citation card · No data'],
    ['planned', 'typed', 'Typed data · Planned'],
    ['planned', 'portal_ppi', "This portal's search · Planned"],
    ['planned', 'external', 'External portal · Planned'],
    ['planned', 'metadata_only', 'Citation card · Planned'],
    ['retired', 'typed', 'Typed data · Retired'],
    ['retired', 'portal_ppi', "This portal's search · Retired"],
    ['retired', 'external', 'External portal · Retired'],
    ['retired', 'metadata_only', 'Citation card · Retired'],
  ]

  it('VW-1: stateWords gives the thirteen distinct strings, rule 4 for another pair and slugs for unknown codes', () => {
    for (const [availability, kind, words] of PAIRS) expect(browserWords.stateWords(kind, availability), `${availability} ${kind}`).toBe(words)
    expect(new Set(PAIRS.map(([availability, kind]) => browserWords.stateWords(kind, availability))).size).toBe(13)
    expect(browserWords.stateWords('external', 'live')).toBe('External portal · Live')
    expect(browserWords.stateWords('syn_kind', 'syn_availability')).toBe('syn kind · syn availability')
  })

  it('VW-2: every word map through its function, an unknown code by its slug, visibility, and the filter words', () => {
    const cases: [(code: string) => string, Readonly<Record<string, string>>, Readonly<Record<string, string>>][] = [
      [browserWords.kindWords, browserWords.KIND_WORDS, KIND_WORDS],
      [browserWords.availabilityWords, browserWords.AVAILABILITY_WORDS, AVAILABILITY_WORDS],
      [browserWords.statusWords, browserWords.STATUS_WORDS, STATUS_WORDS],
      [browserWords.availabilityOptionWords, browserWords.AVAILABILITY_OPTION_WORDS, AVAILABILITY_OPTION_WORDS],
      [browserWords.roleWords, browserWords.ROLE_WORDS, ROLE_WORDS],
      [browserWords.tagWords, browserWords.TAG_WORDS, TAG_WORDS],
    ]
    for (const [words, map, copied] of cases) {
      expect(map).toEqual(copied)
      for (const [code, text] of Object.entries(copied)) expect(words(code), code).toBe(text)
      expect(words('syn_code')).toBe('syn code')
      // An unknown code that names a member every object inherits still reads as its slug.
      expect(words('constructor')).toBe('constructor')
    }
    expect(browserWords.VISIBILITY_WORDS).toEqual(VISIBILITY_WORDS)
    expect(browserWords.visibilityWords('public')).toBeNull()
    expect(browserWords.visibilityWords('staff')).toBe('Staff only')
    expect(browserWords.visibilityWords('unlisted')).toBe('Unlisted')
    expect(browserWords.visibilityWords('syn_visibility')).toBe('syn visibility')
    expect(browserWords.visibilityWords('constructor')).toBe('constructor')
    expect(browserWords.FILTER_LABELS).toEqual(FILTER_LABELS)
    expect(browserWords.ANY_OPTION).toEqual(ANY_OPTION)
  })

  it('VW-3: the card views of the recorded items and the makers, the nested lists and an empty title', () => {
    const [portal, typed] = galleryBody().instances
    const portalView = instanceCardView(portal, vocabularies)
    expect(portalView.state).toBe("This portal's search")
    expect(portalView.synthetic).toBe(false)
    expect(portalView.visibility).toBe('Staff only')
    expect(portalView.facts).toEqual([{ term: 'Biology areas', text: 'Protein interactions' }])
    expect(portalView.action).toEqual({ kind: 'search', text: "Open this portal's search", to: '/search' })
    expect(portalView.href).toBe('/browsers/protein-interactions')
    const typedFacts = [
      { term: 'Cited', text: `${isolateText(PAPER_1)} (synthetic), 2026` },
      { term: 'Relationship types', text: 'Physical interaction, Transcriptional regulation' },
      { term: 'Organisms', text: 'synthetic construct (taxid 32630)' },
      { term: 'Biology areas', text: 'Gene regulation, Protein interactions' },
    ]
    const typedView = instanceCardView(typed, vocabularies)
    expect(typedView.state).toBe('Typed data · Live')
    expect(typedView.synthetic).toBe(true)
    expect(typedView.facts).toEqual(typedFacts)
    expect(typedView.action).toBeNull()
    const table: [GraphInstance, string, boolean, string | null, string | null][] = [
      [plannedInstance, 'Typed data · Planned', true, null, null],
      [plannedPlainInstance, 'Typed data · Planned', false, null, null],
      [retiredInstance, 'Typed data · Retired', true, null, null],
      [externalInstance, 'External portal', false, null, 'external'],
      [citationCardInstance, 'Citation card · No data', true, null, null],
      [unavailableInstance, 'Typed data · Data unavailable', false, null, null],
      [plannedPortalInstance, "This portal's search · Planned", false, null, null],
      [retiredPortalInstance, "This portal's search · Retired", false, null, null],
      [unlistedInstance, 'Typed data · Live', true, 'Unlisted', null],
    ]
    for (const [instance, state, synthetic, visibility, action] of table) {
      const view = instanceCardView(instanceItem(instance), vocabularies)
      expect([view.state, view.synthetic, view.visibility, view.action?.kind ?? null], instance.slug).toEqual([state, synthetic, visibility, action])
    }
    expect(instanceCardView(instanceItem(externalInstance), vocabularies).action).toEqual({
      kind: 'external', lead: 'External portal at', text: 'https://portal.example/browse', href: 'https://portal.example/browse',
    })
    expect(instanceCardView(instanceItem(hostileInstance), vocabularies).action).toEqual({
      kind: 'externalText', lead: 'External portal at', text: 'javascript:alert(1)',
    })
    expect(instanceAction('portal_ppi', 'planned', null)).toBeNull()
    expect(instanceAction('portal_ppi', 'retired', null)).toBeNull()
    const codes = instanceCardView(instanceItem(unknownCodesInstance), vocabularies)
    expect([codes.state, codes.visibility, codes.facts.find((fact) => fact.term === 'Biology areas')?.text]).toEqual([
      'syn kind · syn availability', 'syn visibility', 'syn tag',
    ])
    const a = { id: 1 }
    const b = { id: 2 }
    expect(browserWords.objectItems([a, null, [a], 'x', 3, b] as unknown as { id: number }[])).toEqual([a, b])
    expect(browserWords.stringItems(['x', null, 3, {}, 'y'] as unknown as string[])).toEqual(['x', 'y'])
    for (const odd of [null, undefined, 'x', 3, { length: 1 }] as unknown[]) {
      expect(browserWords.objectItems(odd as { id: number }[])).toEqual([])
      expect(browserWords.stringItems(odd as string[])).toEqual([])
    }
    const nulled = { ...typed, publications: [null, ...typed.publications], tags: [null, ...typed.tags] } as unknown as GraphInstanceItem
    expect(instanceCardView(nulled, vocabularies).facts).toEqual(typedFacts)
    expect(instanceCardView({ ...typed, title: '' }, vocabularies).title).toBe('synthetic-regulation')
    const longCard = instanceCardView({ ...typed, title: LONG, summary: LONG, publications: [{ ...typed.publications[0], title: LONG }] }, vocabularies)
    expect([longCard.title, longCard.summary, longCard.facts[0]]).toEqual([CLIPPED, CLIPPED, { term: 'Cited', text: `${isolateText(CLIPPED)} (synthetic), 2026` }])
  })

  it('VW-4: the badge follows any synthetic part, a paper or a release, never the flag alone', () => {
    expect(instanceCardView(instanceItem(syntheticPaperInstance), vocabularies).synthetic).toBe(true)
    expect(instanceCardView(instanceItem(plainInstance), vocabularies).synthetic).toBe(false)
    expect(instanceHeaderView(makeBrowserResponse(syntheticPaperInstance), vocabularies).synthetic).toBe(true)
    expect(instanceHeaderView(makeBrowserResponse(plainInstance), vocabularies).synthetic).toBe(false)
    const releaseOnly = makeInstance({ is_synthetic: false, publications: [], selection: [{ release: 'release:901', role: 'primary' }] })
    expect(instanceHeaderView(makeBrowserResponse(releaseOnly), vocabularies).synthetic).toBe(true)
    // A card reads no release, so the header's badge above comes from the release alone.
    expect(instanceCardView(instanceItem(releaseOnly), vocabularies).synthetic).toBe(false)
  })

  it('VW-5: the header views, the version and licence lines, the guard and the nested lists', () => {
    const citations = [
      [{ text: `${isolateText(PAPER_1)} (synthetic)` }, { text: '2026' }, { text: 'local key syn-paper-1' }],
      [{ text: `${isolateText(PAPER_2)} (synthetic)` }, { text: '2026' }, { text: 'local key syn-paper-2' }],
    ]
    const mitVersion = {
      text: `${isolateText('Synthetic regulation example v2 (synthetic)')}: ${PRIMARY}`,
      licence: { term: 'Licence', text: 'MIT License', href: MIT },
    }
    const body = typedBody()
    const view = instanceHeaderView(body, vocabularies)
    expect(view.instance).toBe(body.instance)
    expect({
      title: view.title, state: view.state, synthetic: view.synthetic, visibility: view.visibility, summary: view.summary,
      description: view.description, methods: view.methods, citations: view.citations, organisms: view.organisms, versions: view.versions,
      noRelease: view.noRelease, action: view.action, network: view.network,
    }).toEqual({
      title: 'Synthetic regulation example', state: 'Typed data · Live', synthetic: true, visibility: 'Staff only',
      summary: 'Invented regulation and protein interactions for trying the typed graph. They describe no real gene or protein.',
      description: null, methods: null, citations, organisms: ['synthetic construct (taxid 32630)'], versions: [mitVersion],
      noRelease: false, action: null, network: true,
    })
    expect(instanceHeaderView(makeBrowserResponse(twoReleaseInstance), vocabularies).versions).toEqual([
      { text: `${isolateText('Synthetic extras x0 (superseded) (synthetic)')}: ${PRIMARY}`, licence: { term: 'Licence', text: NO_LICENCE } },
      { text: `${isolateText('Test labelled release x1')}: ${CONTEXT}`, licence: { term: 'Licence', text: NO_LICENCE } },
    ])
    expect(instanceHeaderView(makeBrowserResponse(makeInstance({ selection: [{ release: 'release:907', role: 'primary' }] })), vocabularies).versions)
      .toEqual([{ text: `${isolateText('release:907')}: ${PRIMARY}`, licence: null }])
    const unavailable = instanceHeaderView(makeBrowserResponse(unavailableInstance), vocabularies)
    expect([unavailable.noRelease, unavailable.versions, unavailable.network]).toEqual([true, [], false])
    const portal = instanceHeaderView(recorded<GraphBrowserResponse>('browser-portal-ppi').body, vocabularies)
    expect([portal.noRelease, portal.versions, portal.action?.kind, portal.network]).toEqual([false, [], 'search', false])
    expect(instanceHeaderView(makeBrowserResponse(unknownCodesInstance), vocabularies).versions.map((version) => version.text))
      .toEqual([`${isolateText('Synthetic extras x1 (synthetic)')}: syn role`])
    const long = 'd'.repeat(8000)
    expect(instanceHeaderView(makeBrowserResponse(makeInstance({ description: long })), vocabularies).description).toBe(long)
    expect(isInstanceBody(body)).toBe(true)
    expect(isInstanceBody({ ...body, instance: { ...body.instance, summary: null } })).toBe(false)
    const noStatus: Record<string, unknown> = { ...body.instance }
    delete noStatus.status
    expect(isInstanceBody({ ...body, instance: noStatus })).toBe(false)
    expect(isInstanceBody({ ...body, releases: [null] })).toBe(false)
    expect(isInstanceBody({ ...body, releases: [{ ...body.releases[0], dataset: null }] })).toBe(false)
    const nulled = {
      ...body,
      instance: { ...body.instance, publications: [null, ...body.instance.publications], selection: [null, ...body.instance.selection] },
    } as unknown as GraphBrowserResponse
    const skipped = instanceHeaderView(nulled, vocabularies)
    expect([skipped.citations, skipped.versions]).toEqual([citations, [mitVersion]])
    // An entry whose ref or role is not text is skipped as well, so an instance whose every entry is skipped reads as an
    // empty selection does.
    const [recordedEntry] = body.instance.selection
    // The last two hold a number, neither text nor null, so a null check in place of 9.7's text test fails this row.
    const malformed = [
      { role: 'primary' }, { release: null, role: 'primary' }, { release: recordedEntry.release, role: null },
      { release: recordedEntry.release, role: 5 }, { release: 2, role: 'primary' },
    ]
    const withMalformed = {
      ...body,
      instance: { ...body.instance, selection: [...body.instance.selection, ...malformed] },
    } as unknown as GraphBrowserResponse
    const kept = instanceHeaderView(withMalformed, vocabularies)
    expect([kept.versions, kept.noRelease]).toEqual([[mitVersion], false])
    const onlyMalformed = { ...body, instance: { ...body.instance, selection: malformed } } as unknown as GraphBrowserResponse
    const noneKept = instanceHeaderView(onlyMalformed, vocabularies)
    expect([noneKept.versions, noneKept.noRelease]).toEqual([[], true])
    const scoped = { ...body, releases: [{ ...body.releases[0], scopes: [null, 'internal'] }] } as unknown as GraphBrowserResponse
    expect(instanceHeaderView(scoped, vocabularies).versions).toEqual([mitVersion])
    expect(instanceHeaderView({ ...body, instance: { ...body.instance, title: '' } }, vocabularies).title).toBe('synthetic-regulation')
    const longHeader = instanceHeaderView({ ...body, instance: { ...body.instance, title: LONG, summary: LONG } }, vocabularies)
    expect([longHeader.title, longHeader.summary]).toEqual([CLIPPED, CLIPPED])
  })

  it('VW-6: isolateText pins the two characters, and the citation lines are F3\'s but for the title\'s isolates', () => {
    expect(isolateText('SYN')).toBe('\u2068SYN\u2069')
    // A value's own U+2066 to U+2069 go and a paragraph separator becomes a space, so nothing in it ends the pair early.
    expect(isolateText('a\u2069\u202eb')).toBe('\u2068a\u202eb\u2069')
    expect(isolateText('\u2066a\u2067b\u2068c')).toBe('\u2068abc\u2069')
    expect(isolateText('a\nb\rc\u001cd\u001de\u001ef\u0085g\u2029h')).toBe('\u2068a b c d e f g h\u2069')
    expect(isolateText('a\u2028b\tc')).toBe('\u2068a\u2028b\tc\u2069')
    // A value that is not text, which a malformed body can bring, is made text first, as the template literal did.
    expect(isolateText(null as unknown as string)).toBe('\u2068null\u2069')
    expect(isolateText(5 as unknown as string)).toBe('\u20685\u2069')
    const strip = (text: string) => text.split('\u2068').join('').split('\u2069').join('')
    // A paper whose every string passes clipText's 1,000 characters, so that the agreement also holds each cut (2.4).
    const longPaper: GraphPublication = {
      ...papersInstance.publications[0], title: LONG, authors: LONG, journal: LONG, pmid: LONG, doi: LONG, pmcid: LONG, imex_id: LONG,
      local_key: LONG, url: `https://example.org/${LONG}`,
    }
    const publications: GraphPublication[] = [
      ...galleryBody().instances.flatMap((item) => item.publications),
      ...papersInstance.publications, ...syntheticPaperInstance.publications, ...hostileInstance.publications,
      ...hostileTypedInstance.publications, longPaper,
    ]
    expect(publications).toHaveLength(10)
    // So that removing the two characters takes away only what publicationLines added.
    expect(strip(JSON.stringify(publications))).toBe(JSON.stringify(publications))
    for (const publication of publications) {
      const f3 = evidenceRowView(makeGraphEvidenceRow({ publications: [{ role: 'cited', publication }] }), {
        edge: null, releases: new Map(), requested: ['supported'], nodes: [],
      }).fields.find((field) => field.field === 'publications')?.lines
      const f4 = publicationLines(publication).map((line, index) => (index === 0 ? { ...line, text: `cited: ${strip(line.text)}` } : line))
      expect(f4, publication.title).toEqual(f3)
    }
    for (const publication of [...hostileInstance.publications, ...hostileTypedInstance.publications]) {
      const lines = publicationLines(publication)
      expect(last(lines)).toEqual({ text: publication.url })
      expect(lines.filter((line) => line.href !== undefined)).toEqual([])
    }
    expect(hostileInstance.publications[0].url).toBe('data:text/html,<b data-hostile="url">x</b>')
    expect(publicationLines(hostileInstance.publications[0]).some((line) => line.text.includes(hostile('pmid')))).toBe(true)
    expect(firstCitedText(papersInstance.publications[1])).toBe(`${isolateText('title not recorded')} (synthetic)`)
  })

  it('VW-7: taxon words and names, the organism list by the safe-integer rule, and the single taxon', () => {
    expect(taxonWords(32630, vocabularies)).toBe('synthetic construct (taxid 32630)')
    expect(taxonWords(424242, vocabularies)).toBe('taxid 424242')
    expect(taxonWords(9606, null)).toBe('taxid 9606')
    expect(taxonName(32630, vocabularies)).toBe('synthetic construct')
    expect(taxonName(424242, vocabularies)).toBe('taxid 424242')
    expect(taxonName(9606, null)).toBe('taxid 9606')
    expect(organismTaxids([32630], 9606)).toEqual([9606, 32630])
    expect(organismTaxids([7227, 9606], 7227)).toEqual([7227, 9606])
    expect(organismTaxids([], null)).toEqual([])
    expect(organismTaxids(null, undefined)).toEqual([])
    expect(organismTaxids([32630, '9606', null, 1.5] as unknown as number[], undefined)).toEqual([32630])
    expect(singleTaxon([32630], 32630)).toBe(32630)
    expect(singleTaxon([], 32630)).toBe(32630)
    expect(singleTaxon([7227, 9606], 7227)).toBeUndefined()
    expect(singleTaxon([32630], 9606)).toBeUndefined()
    expect(singleTaxon([], null)).toBeUndefined()
    const named = { ...vocabularies, taxa: [{ ...vocabularies.taxa[0], taxid: 777, scientific_name: LONG }] }
    expect([taxonWords(777, named), taxonName(777, named)]).toEqual([`${CLIPPED} (taxid 777)`, CLIPPED])
  })

  it('VW-8: the gate views, the route error views under both word sets, and the words restated from F2 and F3', () => {
    expect(gateView('loading', NOT_ENABLED)).toEqual({ text: PAGE_LOADING, retry: false })
    expect(gateView('off', NOT_ENABLED)).toEqual({ text: NOT_ENABLED, retry: false })
    expect(gateView('unknown', NOT_ENABLED)).toEqual({ text: PAGE_UNKNOWN, retry: true })
    expect(gateView('on', NOT_ENABLED)).toBeNull()
    const gallery = { other: LIST_ERROR, notFound: null, timeout: LIST_TIMEOUT }
    const instance = { other: BROWSER_ERROR, notFound: BROWSER_NOT_AVAILABLE, timeout: BROWSER_TIMEOUT }
    const notFound = answered(404, { detail: 'Not found.' })
    expect(routeErrorView(notFound, gallery)).toEqual({ text: LIST_ERROR, retry: true, recheck: true })
    expect(routeErrorView(notFound, instance)).toEqual({ text: BROWSER_NOT_AVAILABLE, retry: false, recheck: true })
    for (const words of [gallery, instance]) {
      expect(routeErrorView(answered(429, { detail: 't' }, { 'retry-after': '7' }), words))
        .toEqual({ text: 'Too many requests. Try again in 7 seconds.', retry: true, recheck: false })
      expect(routeErrorView(answered(429, { detail: 't' }), words)).toEqual({ text: 'Too many requests. Try again shortly.', retry: true, recheck: false })
      expect(routeErrorView(networkFailure(), words)).toEqual({ text: SERVER_UNREACHABLE, retry: true, recheck: false })
      expect(routeErrorView(answered(500, { detail: 'x' }), words)).toEqual({ text: words.other, retry: true, recheck: false })
      expect(routeErrorView(new GraphBadBodyError(200), words)).toEqual({ text: words.other, retry: true, recheck: false })
      for (const error of [answered(400, { detail: 'x', errors: [] }), answered(401, { detail: 'x' }), answered(406, { detail: 'x' }),
        answered(503, { detail: 'Too expensive.' }), answered(414, '<html></html>'), new GraphRequestTooLongError(5000, 4094)]) {
        expect(routeErrorView(error, words)).toEqual({ text: words.other, retry: false, recheck: false })
      }
    }
    expect(routeErrorView(timedOut(), gallery)).toEqual({ text: LIST_TIMEOUT, retry: true, recheck: false })
    expect(routeErrorView(timedOut(), instance)).toEqual({ text: BROWSER_TIMEOUT, retry: true, recheck: false })
    expect(pageWords.SERVER_UNREACHABLE).toBe(panelErrorText({ kind: 'network' }, 'entity'))
    expect(pageWords.SERVER_UNREACHABLE).toBe(SERVER_UNREACHABLE)
    const throttled: [number | null, string][] = [
      [1, 'Too many requests. Try again in 1 second.'], [7, 'Too many requests. Try again in 7 seconds.'], [null, 'Too many requests. Try again shortly.'],
    ]
    for (const [retryAfter, words] of throttled) {
      expect(pageWords.throttledText(retryAfter)).toBe(panelErrorText({ kind: 'throttled', status: 429, detail: null, retryAfter }, 'entity'))
      expect(pageWords.throttledText(retryAfter)).toBe(words)
    }
    expect(LEGEND_TEXT.synthetic).toBe(`${SYNTHETIC_BADGE}: made up to demonstrate openPIP; it describes no real biology.`)
    expect(badgeWords.SYNTHETIC_BADGE).toBe(SYNTHETIC_BADGE)
  })

  it('VW-9: the gallery view by its rules, the parameter words and the removal words', () => {
    const view = galleryView
    const yearWords = 'The gallery cannot use the publication year in this address.'
    expect(view(address('year=abc'), query(undefined))).toMatchObject({ line: yearWords, removal: ['year'], instances: null })
    // No list request goes out for an invalid address, so a placeholder list never shows beside its words.
    expect(view(address('year=abc'), query(withInstances(2), null, true)))
      .toEqual({ line: yearWords, loading: false, instances: null, removal: ['year'], retry: false })
    const tagProblem = { parameter: 'tag', value: 'foo', allowed: ['gene_regulation'], limit: null }
    expect(view(address('tag=foo'), query(undefined, answered(400, { detail: 'x', errors: [tagProblem] }))))
      .toMatchObject({ line: 'The gallery cannot use the biology area in this address.', removal: ['tag'], retry: false })
    const named = ['year', 'tag', 'tag', 'foo'].map((parameter) => ({ parameter, value: 'x', allowed: [], limit: null }))
    expect(view(address('tag=x&year=2026'), query(undefined, answered(400, { detail: "Unknown tag 'x'.", errors: named }))))
      .toMatchObject({ line: 'The gallery cannot use the biology area and publication year in this address.', removal: ['tag', 'year'], retry: false })
    for (const errors of [[], [{ parameter: 'foo', value: '1', allowed: [], limit: null }]]) {
      expect(view(address('tag=x'), query(undefined, answered(400, { detail: 'x', errors })))).toMatchObject({ line: WHOLE_ADDRESS, removal: [], retry: false })
    }
    for (const error of [new GraphRequestTooLongError(5000, 4094), answered(414, '<html></html>')]) {
      expect(view(address('tag=x'), query(undefined, error))).toMatchObject({ line: WHOLE_ADDRESS, removal: [], retry: false })
    }
    expect(view(address(''), query(undefined))).toEqual({ line: '', loading: true, instances: null, removal: null, retry: false })
    const placeholder = view(address('tag=x'), query(withInstances(2), null, true))
    expect([placeholder.line, placeholder.loading, placeholder.instances?.length]).toEqual(['', true, 2])
    const count: [number, string, string][] = [
      [2, '', '2 browsers listed.'], [1, '', '1 browser listed.'], [0, '', EMPTY_LIST],
      [2, 'q=a', '2 browsers listed.'], [1, 'q=a', '1 browser listed.'], [0, 'q=a', 'No browser matches this search.'],
      [2, 'tag=a', '2 browsers listed.'], [1, 'tag=a', '1 browser listed.'], [0, 'tag=a', 'No browser matches these filters.'],
      [2, 'q=a&tag=b', '2 browsers listed.'], [1, 'q=a&tag=b', '1 browser listed.'], [0, 'q=a&tag=b', 'No browser matches this search and these filters.'],
    ]
    for (const [n, search, line] of count) {
      const shown = view(address(search), query(withInstances(n)))
      expect([shown.line, shown.instances?.length, shown.retry], `${n} ${search}`).toEqual([line, n, false])
    }
    expect(view(address(''), query(withInstances(2, 'syn-next'))).line).toBe(`2 browsers listed. ${INCOMPLETE_LIST}`)
    const item = galleryBody().instances[1]
    for (const data of [{}, { instances: [null] }, { instances: [{ slug: 'syn-bad', kind: 'typed', availability: 'live' }] },
      { instances: [{ ...item, summary: null }] }]) {
      expect(view(address(''), query(data)), JSON.stringify(data).slice(0, 60)).toEqual({ line: LIST_ERROR, loading: false, instances: null, removal: null, retry: true })
    }
    expect(view(address(''), query(undefined, answered(404, { detail: 'Not found.' })))).toMatchObject({ line: LIST_ERROR, retry: true })
    expect(view(address(''), query(undefined, answered(429, { detail: 't' }, { 'retry-after': '7' }))))
      .toMatchObject({ line: 'Too many requests. Try again in 7 seconds.', retry: true })
    expect(view(address(''), query(undefined, timedOut()))).toMatchObject({ line: LIST_TIMEOUT, retry: true })
    // An error comes before the data it failed to refresh.
    expect(view(address(''), query(withInstances(2), answered(500, { detail: 'x' }))))
      .toEqual({ line: LIST_ERROR, loading: false, instances: null, removal: null, retry: true })
    expect(browserWords.paramErrorText(['tag', 'year'])).toBe('The gallery cannot use the biology area and publication year in this address.')
    expect(browserWords.paramErrorText(['q', 'tag', 'year'])).toBe('The gallery cannot use the search, biology area and publication year in this address.')
    expect(browserWords.removalText(['year'])).toBe('Show the gallery without the publication year')
    expect(browserWords.removalText(['tag'])).toBe('Show the gallery without the biology area')
    expect(browserWords.removalText(['q', 'year'])).toBe('Show the gallery without the search and publication year')
    expect(browserWords.removalText([])).toBe('Show the whole gallery')
  })

  it('VW-10: the term rule, the prefix shape, every seed outcome and its line', () => {
    expect(seedWords.termProblem('SYN-TF1')).toBe(false)
    for (const term of ['a,b', 'a\tb', 'a\u0085b']) expect(seedWords.termProblem(term), codePoints(term)).toBe(true)
    // The edges of the two control ranges, U+0000 to U+001F and U+007F to U+009F, and their neighbours.
    for (const term of ['a\u0000b', 'a\nb', 'a\u001fb', 'a\u007fb', 'a\u009fb']) expect(seedWords.termProblem(term), codePoints(term)).toBe(true)
    for (const term of ['a b', 'a~b', 'a\u00a0b', 'Ras:GTP complex']) expect(seedWords.termProblem(term), codePoints(term)).toBe(false)
    for (const term of ['Ras:GTP complex', 'SYN-TF1:x', 'syn:SYN-TF1', 'GO.term:x', 'my_ns:x']) expect(seedWords.PREFIX_SHAPE.test(term), term).toBe(true)
    for (const term of ['SYN-TF1', 'a:', '1a:x']) expect(seedWords.PREFIX_SHAPE.test(term), term).toBe(false)
    const tf1 = resolveBody('resolve-browser-tf1')
    const shared = resolveBody('resolve-browser-shared')
    const base = { problem: false, submitted: true, data: undefined as unknown, error: null as unknown, isFetching: false }
    const outcome = (given: Partial<typeof base>) => seedWords.seedOutcome({ ...base, ...given })
    const result = (status: string, candidates: readonly GraphCandidate[], truncated = false) => ({ results: [{ term: 'x', status, truncated, candidates }] })
    const failure = networkFailure()
    expect(outcome({ problem: true, data: tf1 })).toEqual({ kind: 'problem' })
    expect(outcome({ submitted: false, data: tf1 })).toEqual({ kind: 'idle' })
    expect(outcome({ isFetching: true })).toEqual({ kind: 'searching' })
    expect(outcome({ isFetching: true, error: failure })).toEqual({ kind: 'searching' })
    expect(outcome({ isFetching: true, data: tf1 })).toEqual({ kind: 'resolved', candidate: tf1.results[0].candidates[0] })
    expect(outcome({ error: failure })).toEqual({ kind: 'error', error: failure })
    expect(outcome({ data: {} })).toEqual({ kind: 'error', error: null })
    expect(outcome({ data: tf1 })).toEqual({ kind: 'resolved', candidate: tf1.results[0].candidates[0] })
    expect(outcome({ data: resolveBody('resolve-browser-nope') })).toEqual({ kind: 'unmatched' })
    expect(outcome({ data: result('resolved', []) })).toEqual({ kind: 'unmatched' })
    expect(outcome({ data: result('ambiguous', shared.results[0].candidates, true) }))
      .toEqual({ kind: 'choose', status: 'ambiguous', candidates: shared.results[0].candidates, truncated: true })
    expect(outcome({ data: result('candidate', tf1.results[0].candidates) }))
      .toEqual({ kind: 'choose', status: 'candidate', candidates: tf1.results[0].candidates, truncated: false })
    expect(outcome({ data: result('syn_status', shared.results[0].candidates) }).kind).toBe('choose')
    const context = {
      term: 'SYN-TF1', name: TF1 as string | null, chosen: false, atLimit: false, max: 10, pending: false, pickedName: null as string | null,
      organism: null as string | null,
    }
    const text = (given: seedWords.SeedOutcome, more: Partial<typeof context> = {}) => seedWords.seedOutcomeText(given, { ...context, ...more })
    expect(text({ kind: 'idle' })).toBe('')
    expect(text({ kind: 'problem' })).toBe(ONE_TERM)
    expect(text({ kind: 'searching' })).toBe(SEARCHING)
    expect(text({ kind: 'error', error: null })).toBe(`${SEARCH_FAILED} ${SEARCH_AGAIN}`)
    expect(text({ kind: 'error', error: answered(429, { detail: 't' }, { 'retry-after': '7' }) })).toBe('Too many requests. Try again in 7 seconds.')
    expect(text({ kind: 'error', error: networkFailure() })).toBe(`${SERVER_UNREACHABLE} ${SEARCH_AGAIN}`)
    expect(text({ kind: 'error', error: timedOut() })).toBe(`${SEARCH_TIMEOUT} ${SEARCH_AGAIN}`)
    expect(text({ kind: 'error', error: timedOut() })).toBe('The search did not finish in time. Press Search to try again.')
    for (const error of [answered(404, { detail: 'Not found.' }), answered(400, { detail: 'x', errors: [] }), answered(406, { detail: 'x' }),
      answered(401, { detail: 'x' }), answered(414, '<html></html>'), new GraphRequestTooLongError(5000, 4094)]) {
      expect(text({ kind: 'error', error })).toBe(SEARCH_FAILED)
    }
    expect(text({ kind: 'error', error: answered(500, { detail: 'x' }) })).toBe(`${SEARCH_FAILED} ${SEARCH_AGAIN}`)
    const resolved: seedWords.SeedOutcome = { kind: 'resolved', candidate: tf1.results[0].candidates[0] }
    expect(text(resolved, { chosen: true })).toBe(chosenText(TF1))
    expect(text(resolved, { chosen: true, atLimit: true })).toBe(chosenText(TF1))
    expect(text(resolved, { atLimit: true, max: 1 })).toBe(LIMIT_ONE)
    expect(text(resolved, { pending: true })).toBe(SEARCHING)
    expect(text(resolved, { chosen: true, pending: true })).toBe(chosenText(TF1))
    expect(text(resolved, { atLimit: true, max: 1, pending: true })).toBe(LIMIT_ONE)
    expect(text(resolved)).toBe(notChosenText(TF1))
    const candidate = `“${isolateText('syn-tf1')}” matches one entity, though not as its primary identifier is written. Add it if it is the one you meant.`
    const ambiguous = `“${isolateText('SYN-SHARED')}” matches more than one entity. Add the one you meant.`
    const listed = 'More entities match than the 2 listed; search with an identifier to narrow them.'
    for (const [status, term, words] of [['candidate', 'syn-tf1', candidate], ['ambiguous', 'SYN-SHARED', ambiguous]]) {
      const choose = (cut: boolean): seedWords.SeedOutcome => ({ kind: 'choose', status, candidates: shared.results[0].candidates, truncated: cut })
      expect(text(choose(false), { term })).toBe(words)
      expect(text(choose(true), { term })).toBe(`${words} ${listed}`)
      expect(text(choose(false), { term, atLimit: true, max: 1 })).toBe(`${words} ${LIMIT_ONE}`)
      expect(text(choose(true), { term, atLimit: true, max: 10 })).toBe(`${words} ${listed} ${LIMIT_TEN}`)
      expect(text(choose(false), { term, pickedName: TF1 })).toBe(`${words} ${chosenText(TF1)}`)
      expect(text(choose(true), { term, pickedName: TF1 })).toBe(`${words} ${chosenText(TF1)} ${listed}`)
      expect(text(choose(false), { term, pickedName: TF1, atLimit: true, max: 1 })).toBe(`${words} ${chosenText(TF1)} ${LIMIT_ONE}`)
      expect(text(choose(true), { term, pickedName: TF1, atLimit: true, max: 10 })).toBe(`${words} ${chosenText(TF1)} ${listed} ${LIMIT_TEN}`)
    }
    expect(text({ kind: 'unmatched' }, { term: 'SYN-NOPE' })).toBe(`No entity in this browser matches “${isolateText('SYN-NOPE')}”.`)
    expect(text({ kind: 'unmatched' }, { term: 'SYN-NOPE', organism: 'synthetic construct' }))
      .toBe(`No ${isolateText('synthetic construct')} entity in this browser matches “${isolateText('SYN-NOPE')}”.`)
    const nope = outcome({ data: resolveBody('resolve-browser-nope') })
    expect(text(nope, { term: 'Ras:GTP complex' })).toBe(`No entity in this browser matches “${isolateText('Ras:GTP complex')}”. ${PREFIX_NOTE}`)
    expect(text(nope, { term: 'Ras:GTP complex', organism: 'synthetic construct' }))
      .toBe(`No ${isolateText('synthetic construct')} entity in this browser matches “${isolateText('Ras:GTP complex')}”. ${PREFIX_NOTE}`)
    expect(seedWords.limitText(1)).toBe(LIMIT_ONE)
    expect(seedWords.limitText(10)).toBe(LIMIT_TEN)
  })

  it('VW-11: the recorded resolve bodies, their names, and each bad body refused whole', () => {
    const outcome = (data: unknown) => seedWords.seedOutcome({ problem: false, submitted: true, data, error: null, isFetching: false })
    const tf1 = resolveBody('resolve-browser-tf1')
    const tf1Case = resolveBody('resolve-browser-tf1-case')
    const shared = resolveBody('resolve-browser-shared')
    const resolved = outcome(tf1)
    expect(resolved.kind).toBe('resolved')
    if (resolved.kind === 'resolved') expect(endName(resolved.candidate.entity, null, vocabularies)).toBe(TF1)
    const candidate = outcome(tf1Case)
    expect(candidate.kind === 'choose' ? [candidate.status, candidate.candidates.length] : null).toEqual(['candidate', 1])
    if (candidate.kind === 'choose') expect(endName(candidate.candidates[0].entity, null, vocabularies)).toBe(TF1)
    const ambiguous = outcome(shared)
    expect(ambiguous.kind === 'choose' ? [ambiguous.status, ambiguous.candidates.map((item) => item.entity.id)] : null)
      .toEqual(['ambiguous', ['entity:6', 'entity:13']])
    if (ambiguous.kind === 'choose') {
      expect(ambiguous.candidates.map((item) => endName(item.entity, null, vocabularies))).toEqual(['SYN-SHARED (Gene)', 'SYN-SHARED (Protein (UniProtKB entry))'])
    }
    expect(outcome(resolveBody('resolve-browser-nope')).kind).toBe('unmatched')
    const first = tf1.results[0]
    const noEntity: Record<string, unknown> = { ...first.candidates[0] }
    delete noEntity.entity
    const bad = [
      { ...tf1, results: [{ ...first, candidates: [noEntity] }] },
      { ...tf1, results: [{ ...first, candidates: [{ ...first.candidates[0], entity: { ...first.candidates[0].entity, id: 7 } }] }] },
      // The fields endName reads, each of the wrong type, which would otherwise throw during render.
      { ...tf1, results: [{ ...first, candidates: [{ ...first.candidates[0], entity: { ...first.candidates[0].entity, label: null } }] }] },
      { ...tf1, results: [{ ...first, candidates: [{ ...first.candidates[0], entity: { ...first.candidates[0].entity, type: 7 } }] }] },
      { ...tf1, results: [{ ...first, candidates: [{ ...first.candidates[0], entity: { ...first.candidates[0].entity, primary_identifier: { namespace: 'synthetic_gene', value: null } } }] }] },
      { ...shared, results: [{ ...shared.results[0], candidates: [shared.results[0].candidates[0], null, shared.results[0].candidates[1]] }] },
      { ...tf1, results: [] },
    ]
    for (const data of bad) {
      const refused = outcome(data)
      expect(refused).toEqual({ kind: 'error', error: null })
      expect(seedWords.seedOutcomeText(refused, { term: 'x', name: null, chosen: false, atLimit: false, max: 10, pending: false, pickedName: null, organism: null }))
        .toBe('The search could not be run. Press Search to try again.')
    }
  })

  it('VW-12: no word says not enabled but the four off sentences, and none says what 3.8 forbids', () => {
    const strings = [pageWords, browserWords, seedWords, badgeWords, workspaceWords].flatMap((module) =>
      Object.entries(module).flatMap(([name, value]): [string, string][] => {
        if (typeof value === 'string') return [[name, value]]
        if (typeof value === 'object' && value !== null && !Array.isArray(value) && !(value instanceof RegExp)) {
          return Object.entries(value).filter(([, entry]) => typeof entry === 'string').map(([key, entry]): [string, string] => [`${name}.${key}`, entry as string])
        }
        return []
      }))
    expect(strings.length).toBeGreaterThan(60)
    const names = ['typed', 'portal_ppi', 'external', 'metadata_only', 'live', 'planned', 'retired', 'available', 'staff', 'unlisted', 'public',
      'primary', 'context', 'gene_regulation', 'syn_code']
    const candidates = resolveBody('resolve-browser-shared').results[0].candidates
    const outcomes: seedWords.SeedOutcome[] = [
      { kind: 'idle' }, { kind: 'problem' }, { kind: 'searching' }, { kind: 'unmatched' },
      { kind: 'resolved', candidate: candidates[0] },
      { kind: 'choose', status: 'candidate', candidates, truncated: true }, { kind: 'choose', status: 'ambiguous', candidates, truncated: false },
      ...[null, answered(404, { detail: 'Not found.' }), answered(429, { detail: 't' }), answered(500, { detail: 'x' }), answered(503, { detail: 'x' }),
        answered(400, { detail: 'x', errors: [] }), answered(414, '<html></html>'), networkFailure(), timedOut(), new GraphBadBodyError(200),
        new GraphRequestTooLongError(5000, 4094)].map((error): seedWords.SeedOutcome => ({ kind: 'error', error })),
    ]
    const contexts = [
      { term: 'SYN-NOPE', name: TF1, chosen: false, atLimit: false, max: 10, pending: false, pickedName: null, organism: null },
      { term: 'Ras:GTP complex', name: TF1, chosen: true, atLimit: true, max: 1, pending: false, pickedName: TF1, organism: 'synthetic construct' },
      { term: 'syn-tf1', name: TF1, chosen: false, atLimit: false, max: 10, pending: true, pickedName: null, organism: null },
    ]
    const called = [
      browserWords.countText(0), browserWords.countText(1), browserWords.countText(2),
      browserWords.noMatchText(true, false), browserWords.noMatchText(false, true), browserWords.noMatchText(true, true), browserWords.noMatchText(false, false),
      browserWords.paramErrorText([]), browserWords.paramErrorText(['q', 'tag', 'relationship_type', 'taxon', 'assay', 'year', 'status', 'availability']),
      browserWords.removalText([]), browserWords.removalText(['q', 'status', 'availability']),
      ...names.flatMap((code) => [browserWords.kindWords(code), browserWords.availabilityWords(code), browserWords.statusWords(code),
        browserWords.availabilityOptionWords(code), browserWords.tagWords(code), browserWords.roleWords(code), browserWords.visibilityWords(code) ?? '']),
      ...PAIRS.map(([availability, kind]) => browserWords.stateWords(kind, availability)),
      browserWords.versionLine('SYN release v1', 'primary'), browserWords.versionLine('SYN release v1', 'syn_role'),
      seedWords.chosenText(TF1), seedWords.notChosenText(TF1), seedWords.candidateText('syn-tf1'), seedWords.ambiguousText('SYN-SHARED'),
      seedWords.unmatchedText('SYN-NOPE', null), seedWords.unmatchedText('SYN-NOPE', 'synthetic construct'), seedWords.truncatedText(20),
      seedWords.limitText(1), seedWords.limitText(10), seedWords.addText(TF1), seedWords.removeText(TF1),
      ...outcomes.flatMap((given) => contexts.map((context) => seedWords.seedOutcomeText(given, context))),
      pageWords.throttledText(1), pageWords.throttledText(7), pageWords.throttledText(null),
    ]
    const all: [string, string][] = [...strings, ...called.map((value, index): [string, string] => [`call ${index}`, value])]
    // X1's three off sentences join F4's (X1 spec 12.11; Q24): each is a gate's notEnabled, the only words that say it.
    const offSentences = ['BROWSERS_NOT_ENABLED', 'EXPLORE_NOT_ENABLED', 'ENTITY_NOT_ENABLED', 'ENTITY_LOOKUP_NOT_ENABLED']
    const exported: Record<string, unknown> = Object.fromEntries(Object.entries(pageWords))
    expect(offSentences.map((name) => exported[name])).toEqual([
      'Browsers are not enabled on this portal.', 'The unified network is not enabled on this portal.',
      'Entity pages are not enabled on this portal.', 'Finding an entity by its identifier is not enabled on this portal.',
    ])
    expect(all.filter(([name, value]) => !offSentences.includes(name) && value.includes('not enabled'))).toEqual([])
    expect(all.filter(([, value]) => /does not exist|not found|undefined|HTTP|No page uses|\b[Nn]one\b/.test(value))).toEqual([])
    expect(browserWords.stateWords('typed', 'metadata_only')).not.toContain('Citation card')
  })
})
