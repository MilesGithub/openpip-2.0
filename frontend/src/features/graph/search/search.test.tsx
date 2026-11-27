import { act, cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { QueryClient } from '@tanstack/react-query'
import { AxiosError } from 'axios'
import { http, HttpResponse } from 'msw'
import type { JsonBodyType } from 'msw'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { GraphRequestTooLongError } from '../../../api/graph/errors'
import { chunkResolveTerms } from '../../../api/graph/resolve'
import { graphStatusOptions } from '../../../api/graph/status'
import {
  MANY_SEARCH_TERMS, cutResolve, makeGraphNode, makeResolveCandidate, makeResolveResponse, manyTermResolve,
} from '../../../mocks/fixtures/typedGraph/extras'
import { server } from '../../../mocks/server'
import { useAuthStore } from '../../../store/authStore'
import { GRAPH_WAIT, graphWrapper, newGraphClient, recordRequests, recorded } from '../../../test/graphHooks'
import type { RequestLog } from '../../../test/graphHooks'
import { recordAnswers } from '../../../test/graphRoutes'
import * as seedWords from '../../browsers/seedWords'
import { INPUT_STYLE } from '../pages/pageStyle'
import type { GraphCandidate, GraphMatch, GraphNode, GraphResolveResponse, GraphStatus, GraphVocabularies } from '../types'
import { isolateText } from '../words/isolateText'
import { EntitySearch } from './EntitySearch'
import type { EntitySearchProps } from './EntitySearch'
import {
  SEARCH_TERM_MAX_CHARS, autoAdds, candidateMatch, candidateOrganism, chunkOutcome, entityTypeOptions, searchLimits, searchMayRetry,
  searchTaxonOptions, splitSearch, termOutcomes,
} from './searchModel'
import type { TermOutcome } from './searchModel'
import * as searchWords from './searchWords'

// X1 spec 12.3 (XS): 12.0 item 1's hooks, with no scrollTo spy, since nothing navigates.
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
  // Unmounted first, so that what the unmount reports is checked too.
  cleanup()
  requests.stop()
  answers.stop()
  const calls = [...vi.mocked(console.warn).mock.calls, ...vi.mocked(console.error).mock.calls]
  vi.restoreAllMocks()
  expect(requests.unhandled).toEqual([])
  expect(answers.answers.filter((answer) => answer.startsWith('501 '))).toEqual([])
  expect(calls.map((args) => args.map(String).join(' ').slice(0, 300))).toEqual([])
})

// Words copied from the spec's section 7.12, F4's restatements and 12.0's BADGE_LINE, never from the module under test.
const SEARCH_FORM_NAME = 'Search for entities'
const TERMS_LABEL = 'Names or identifiers'
const TERMS_HINT = 'Separate several with commas or new lines. A name that holds a comma or a tab cannot be searched: use an identifier.'
const ENTITY_TYPE_LABEL = 'Entity type'
const ANY_ENTITY_TYPE = 'Any entity type'
const SEARCH_TAXON_LABEL = 'Organism of the entities to find'
const SEARCH_ANY_ORGANISM = 'Any organism'
const SEARCH_BUTTON = 'Search'
const SEARCHING = 'Searching…'
const RESULTS_NAME = 'Search results'
const NO_TERM_SENT = 'No term was searched.'
const SEARCH_TIMEOUT = 'The search did not finish in time.'
const SEARCH_TOO_LONG = 'The search would be too long to send: search with shorter terms or fewer releases.'
const PREFIX_NOTE = "A term that starts with a word and a colon is read as an identifier with its namespace's prefix: to find a name written that way, search for an identifier."
const CHOSEN_MARK = ' (chosen)'
const MATCH_KIND_WORDS = {
  primary: 'its primary identifier', secondary: 'a secondary identifier', xref: 'a cross-reference', symbol: 'a symbol', alias: 'an alias', label: 'its label',
}
const CASE_NOTE = ' in another case'
const VERSION_NOTE = ' with another version'
const TRY_AGAIN = 'Try again'
const THROTTLED_SEVEN = 'Too many requests. Try again in 7 seconds.'
const BADGE_LINE = 'Synthetic demonstration data Made up to demonstrate openPIP; it describes no real biology.'
const SYNTHETIC_BADGE = 'Synthetic demonstration data'
const SYNTHETIC_FACTS = 'synthetic construct (taxid 32630); matched by its label'
const i = isolateText
const plural = (n: number, one: string, many: string) => (n === 1 ? one : many.replace('{n}', String(n)))
const cutText = (n: number) => plural(n, 'For 1 term more entities match than are listed: search with an identifier to narrow it.',
  'For {n} terms more entities match than are listed: search with identifiers to narrow them.')
const failedText = (n: number) => plural(n, '1 term could not be searched.', '{n} terms could not be searched.')
const resolvedChosenText = (term: string, name: string) => `“${i(term)}” is ${i(name)}, among the chosen entities.`
const resolvedNotChosenText = (term: string, name: string) => `“${i(term)}” is ${i(name)}, not among the chosen entities. Press Search to add it.`
const resolvedAddingText = (term: string, name: string) => `“${i(term)}” is ${i(name)}; adding it…`
const limitText = (max: number) => `${plural(max, 'The network takes at most 1 entity.', 'The network takes at most {n} entities.')} Remove one to add another.`
const resolvedLimitText = (term: string, name: string, max: number) => `“${i(term)}” is ${i(name)}, not added. ${limitText(max)}`
const candidateText = (term: string) => `“${i(term)}” matches one entity, though not as its primary identifier is written. Add it if it is the one you meant.`
const ambiguousText = (term: string) => `“${i(term)}” matches more than one entity. Add the one you meant.`
const chosenText = (name: string) => `${i(name)} is among the chosen entities.`
const truncatedText = (listed: number) => `More entities match than the ${listed} listed; search with an identifier to narrow them.`
const unmatchedText = (term: string, organism: string | null, scoped: boolean) => {
  const scope = scoped ? ' in the releases this view reads' : ''
  return organism === null ? `No entity available to you${scope} matches “${i(term)}”.` : `No ${i(organism)} entity available to you${scope} matches “${i(term)}”.`
}
/** The term as clipText shows it, given here already cut. */
const controlTermText = (shown: string) => `“${i(shown)}” holds a control character, so it was not searched.`
const unsendableTermText = (shown: string) => `“${i(shown)}” holds a character that cannot be sent, so it was not searched.`
const longTermText = (shown: string, max: number) => `“${i(shown)}” is longer than ${plural(max, '1 character', '{n} characters')}, so it was not searched.`
const termSearchingText = (term: string) => `Searching for “${i(term)}”…`
const termFailedText = (term: string) => `“${i(term)}” could not be searched.`
const matchesName = (term: string) => `Entities matching “${i(term)}”`
const addText = (name: string) => `Add ${i(name)}`

const RESOLVE = '/api/graph/v1/resolve'
const RESOLVE_TF1_SHARED = '/api/graph/v1/resolve?q=SYN-TF1,SYN-SHARED'
const RESOLVE_NOPE = '/api/graph/v1/resolve?q=SYN-NOPE'
const BANNED = /does not exist|not found|undefined|HTTP|No page uses|\b[Nn]one\b/
const vocab = recorded<GraphVocabularies>('vocabularies').body
const statusOn = () => recorded<GraphStatus>('status-on').body
const resolveBody = (name: string) => recorded<GraphResolveResponse>(name).body
const copy = <T,>(value: T): T => JSON.parse(JSON.stringify(value)) as T
const asked = () => requests.urls.map((url) => url.pathname + url.search)
const resolveAsked = () => asked().filter((url) => url.startsWith(RESOLVE))
const pause = (ms: number) => act(async () => {
  await new Promise((resolve) => setTimeout(resolve, ms))
})
// Errors as F4's VW rows build them (BR/views.test.ts:125-129).
function answered(status: number, data: unknown, headers: Record<string, string> = {}): AxiosError {
  return new AxiosError('x', undefined, undefined, undefined, { status, statusText: '', headers, config: {} as never, data })
}
const networkFailure = () => new AxiosError('x', 'ERR_NETWORK')
const timedOut = () => new AxiosError('x', 'ECONNABORTED')
const LIMITS = { maxTerms: 25, maxTermChars: 200 }
/** XS-13's terms: 120 Greek letters each, two UTF-8 bytes per letter. */
const GREEK = Array.from({ length: 25 }, (_, index) => 'α'.repeat(119) + String.fromCodePoint(0x3b1 + index))
const SLICES = [0, 5, 10, 15, 20].map((start) => GREEK.slice(start, start + 5))

const box = () => screen.getByRole('textbox', { name: TERMS_LABEL }) as HTMLTextAreaElement
const line = () => screen.getByRole('status')
const lines = () => Array.from(screen.getByRole('list', { name: RESULTS_NAME }).children, (item) => item.querySelector('p')?.textContent ?? '')
const lineIs = (text: string) => waitFor(() => expect(line().textContent).toBe(text), GRAPH_WAIT)
const badgeParagraphs = () => Array.from(document.querySelectorAll('p')).filter((node) => node.textContent === BADGE_LINE)
const following = (first: Node, second: Node) => (first.compareDocumentPosition(second) & Node.DOCUMENT_POSITION_FOLLOWING) !== 0
const qsOf = (urls: string[]) => urls.map((url) => new URL(url, 'http://x').searchParams.get('q') ?? '')

function held() {
  let release: () => void = () => {}
  const promise = new Promise<void>((resolve) => {
    release = resolve
  })
  return { promise, release: () => release() }
}

function clientWith(status: GraphStatus = statusOn()): QueryClient {
  const client = newGraphClient()
  client.setQueryData(graphStatusOptions().queryKey, status)
  return client
}

type Props = Partial<EntitySearchProps> & { client?: QueryClient }
function renderSearch(props: Props = {}) {
  const { client = clientWith(), ...rest } = props
  const onAdd = vi.fn()
  const all = (more: Partial<EntitySearchProps>): EntitySearchProps => ({
    selection: {}, chosen: [], maxChosen: 10, onAdd, vocabularies: vocab, ...rest, ...more,
  })
  const view = render(<EntitySearch {...all({})} />, { wrapper: graphWrapper(client) })
  return { ...view, client, onAdd, again: (more: Partial<EntitySearchProps>) => view.rerender(<EntitySearch {...all(more)} />) }
}

async function searchFor(user: ReturnType<typeof userEvent.setup>, text: string) {
  await user.clear(box())
  await user.click(box())
  if (text !== '') await user.paste(text)
  await user.click(screen.getByRole('button', { name: SEARCH_BUTTON }))
}

/** Answers each request with makeResolveResponse of its own terms, or with what `pick` gives for it. */
function serveOwnTerms(pick: (terms: string[]) => JsonBodyType | Response | undefined = () => undefined) {
  server.use(http.get(RESOLVE, ({ request }) => {
    const terms = (new URL(request.url).searchParams.get('q') ?? '').split(',')
    const picked = pick(terms)
    if (picked instanceof Response) return picked
    return HttpResponse.json(picked ?? makeResolveResponse(terms))
  }))
}

const context = (more: Partial<searchWords.TermContext> = {}): searchWords.TermContext => ({
  name: null, chosen: false, atLimit: false, max: 10, pending: false, pickedName: null, organism: null, scoped: false, maxTermChars: 200, ...more,
})
const outcomesOf = (bodies: unknown[], sizes: number[]) => bodies.map((data, index) => chunkOutcome({ data, error: null, isFetching: false, size: sizes[index] }))

/** XS-8's word-function calls, each with the words 7.12 gives it: XS-8 checks them and XS-9 bans words in them. */
function wordPairs(): [string, string][] {
  const pairs: [string, string][] = []
  const check = (got: string, want: string) => {
    pairs.push([got, want])
  }
  check(searchWords.searchedText(1), '1 term searched.')
  check(searchWords.searchedText(2), '2 terms searched.')
  check(searchWords.overCapText(1, 25), '1 more term was not searched: a search takes at most 25 terms.')
  check(searchWords.overCapText(2, 1), '2 more terms were not searched: a search takes at most 1 term.')
  check(searchWords.cutText(1), 'For 1 term more entities match than are listed: search with an identifier to narrow it.')
  check(searchWords.cutText(2), 'For 2 terms more entities match than are listed: search with identifiers to narrow them.')
  check(searchWords.failedText(1), '1 term could not be searched.')
  check(searchWords.failedText(5), '5 terms could not be searched.')
  check(searchWords.searchFailureWords(null), '')
  check(searchWords.searchFailureWords(answered(429, { detail: 't' }, { 'retry-after': '7' })), THROTTLED_SEVEN)
  check(searchWords.searchFailureWords(answered(429, { detail: 't' })), 'Too many requests. Try again shortly.')
  check(searchWords.searchFailureWords(networkFailure()), 'The server could not be reached.')
  check(searchWords.searchFailureWords(timedOut()), SEARCH_TIMEOUT)
  check(searchWords.searchFailureWords(new GraphRequestTooLongError(5000, 4094)), SEARCH_TOO_LONG)
  check(searchWords.searchFailureWords(answered(414, '<html></html>')), SEARCH_TOO_LONG)
  check(searchWords.searchFailureWords(answered(400, { detail: 'x', errors: [] })), '')
  check(searchWords.searchFailureWords(answered(500, { detail: 'x' })), '')
  const summary = (more: Partial<searchWords.SearchSummary> = {}): searchWords.SearchSummary => ({
    submitted: true, searching: false, searched: 0, over: 0, maxTerms: 25, cut: 0, failed: 0, failure: undefined,
    chosen: 0, notAdded: 0, adding: 0, choose: 0, unmatched: 0, ...more,
  })
  check(searchWords.outcomeCountsText(summary()), '')
  check(searchWords.outcomeCountsText(summary({ chosen: 1 })), '1 is among the chosen entities.')
  check(searchWords.outcomeCountsText(summary({ chosen: 2, adding: 1, notAdded: 1, choose: 1, unmatched: 1 })),
    '2 are among the chosen entities; 1 is being added; 1 was not added: the network takes no more; 1 needs a choice from its list below; 1 matched no entity available to you.')
  check(searchWords.outcomeCountsText(summary({ adding: 2, notAdded: 2, choose: 2, unmatched: 2 })),
    '2 are being added; 2 were not added: the network takes no more; 2 need a choice from their lists below; 2 matched no entity available to you.')
  check(searchWords.searchStatusText(summary({ submitted: false })), '')
  check(searchWords.searchStatusText(summary({ searching: true })), SEARCHING)
  check(searchWords.searchStatusText(summary()), 'No term was searched.')
  check(searchWords.searchStatusText(summary({ failed: 1, failure: answered(500, { detail: 'x' }) })), 'No term was searched. 1 term could not be searched.')
  check(searchWords.searchStatusText(summary({
    searched: 20, failed: 5, over: 2, cut: 1, failure: answered(429, { detail: 't' }, { 'retry-after': '7' }), chosen: 1, choose: 1,
  })), '20 terms searched. 1 is among the chosen entities; 1 needs a choice from its list below. 2 more terms were not searched: a search takes at most 25 terms. For 1 term more entities match than are listed: search with an identifier to narrow it. 5 terms could not be searched. Too many requests. Try again in 7 seconds.')
  const term = 'SYN-TF1'
  const name = 'SYN-TF1 (Gene)'
  check(searchWords.resolvedChosenText(term, name), resolvedChosenText(term, name))
  check(searchWords.resolvedNotChosenText(term, name), resolvedNotChosenText(term, name))
  check(searchWords.resolvedAddingText(term, name), resolvedAddingText(term, name))
  for (const max of [10, 1]) {
    check(searchWords.limitText(max), limitText(max))
    check(searchWords.resolvedLimitText(term, name, max), resolvedLimitText(term, name, max))
  }
  check(searchWords.candidateText(term), candidateText(term))
  check(searchWords.ambiguousText(term), ambiguousText(term))
  check(searchWords.chosenText(name), chosenText(name))
  check(searchWords.truncatedText(20), truncatedText(20))
  for (const organism of [null, 'synthetic construct']) {
    for (const scoped of [false, true]) check(searchWords.unmatchedText(term, organism, scoped), unmatchedText(term, organism, scoped))
  }
  check(searchWords.unmatchedText('SYN-NOPE', null, true), `No entity available to you in the releases this view reads matches “${isolateText('SYN-NOPE')}”.`)
  check(searchWords.controlTermText(term), controlTermText(term))
  check(searchWords.unsendableTermText('a\uD800b'), unsendableTermText('a\uD800b'))
  check(searchWords.longTermText('a'.repeat(1001), 200), longTermText(`${'a'.repeat(1000)}… (1 more character not shown)`, 200))
  check(searchWords.controlTermText('\u0007' + 'a'.repeat(1000)), controlTermText(`\u0007${'a'.repeat(999)}… (1 more character not shown)`))
  check(searchWords.unsendableTermText('\uD800' + 'a'.repeat(1000)), unsendableTermText(`\uD800${'a'.repeat(999)}… (1 more character not shown)`))
  check(searchWords.termSearchingText(term), termSearchingText(term))
  check(searchWords.termFailedText(term), termFailedText(term))
  check(searchWords.matchesName(term), matchesName(term))
  check(searchWords.addText(name), addText(name))
  const match = (more: Partial<GraphMatch>): GraphMatch => ({ kind: 'label', namespace: null, value: 'x', case_exact: true, version_mismatch: false, ...more })
  check(searchWords.matchText(match({})), 'matched by its label')
  check(searchWords.matchText(match({ kind: 'primary', case_exact: false })), 'matched by its primary identifier in another case')
  check(searchWords.matchText(match({ kind: 'primary', case_exact: false, version_mismatch: true })), 'matched by its primary identifier in another case with another version')
  check(searchWords.matchText(match({ kind: 'syn_kind' as GraphMatch['kind'] })), 'matched by syn kind')
  check(searchWords.candidateFactsText('synthetic construct (taxid 32630)', match({})), 'synthetic construct (taxid 32630); matched by its label')
  check(searchWords.candidateFactsText('synthetic construct (taxid 32630)', null), 'synthetic construct (taxid 32630)')
  check(searchWords.candidateFactsText(null, match({})), 'matched by its label')
  check(searchWords.candidateFactsText(null, null), '')
  // termLineText over every outcome and context of 7.12's row, in F4's order of rules.
  const tf1 = resolveBody('resolve-tf1').results[0].candidates[0]
  const shared = resolveBody('resolve-tf1-shared').results[1].candidates
  const line = (outcome: TermOutcome, more: Partial<searchWords.TermContext> = {}, text = term) => searchWords.termLineText(text, outcome, context(more))
  check(line({ kind: 'refused', reason: 'control' }), controlTermText(term))
  check(line({ kind: 'refused', reason: 'unsendable' }), unsendableTermText(term))
  check(line({ kind: 'refused', reason: 'long' }, { maxTermChars: 5 }), longTermText(term, 5))
  check(line({ kind: 'searching' }), termSearchingText(term))
  check(line({ kind: 'failed', error: null }), termFailedText(term))
  const resolved: TermOutcome = { kind: 'resolved', candidate: tf1 }
  check(line(resolved, { name, chosen: true, atLimit: true, pending: true }), resolvedChosenText(term, name))
  check(line(resolved, { name, atLimit: true, pending: true }), resolvedLimitText(term, name, 10))
  check(line(resolved, { name, pending: true }), resolvedAddingText(term, name))
  check(line(resolved, { name }), resolvedNotChosenText(term, name))
  check(line(resolved, {}), resolvedNotChosenText(term, ''))
  check(line({ kind: 'choose', status: 'candidate', candidates: [tf1], truncated: false }), candidateText(term))
  check(line({ kind: 'choose', status: 'ambiguous', candidates: shared, truncated: true }, { pickedName: 'SYN-SHARED (Gene)', atLimit: true }),
    `${ambiguousText(term)} ${chosenText('SYN-SHARED (Gene)')} ${truncatedText(2)} ${limitText(10)}`)
  check(line({ kind: 'choose', status: 'syn_status', candidates: shared, truncated: false }), ambiguousText(term))
  check(line({ kind: 'unmatched' }, { organism: 'synthetic construct', scoped: true }), unmatchedText(term, 'synthetic construct', true))
  check(line({ kind: 'unmatched' }, {}, 'Ras:GTP complex'), `${unmatchedText('Ras:GTP complex', null, false)} ${PREFIX_NOTE}`)
  return pairs
}

describe('the entity search', () => {
  it('XS-1: the search\'s caps come from status, with fallbacks', () => {
    expect(SEARCH_TERM_MAX_CHARS).toBe(200)
    const on = statusOn()
    expect(searchLimits(on)).toEqual({ maxTerms: 25, maxTermChars: 200 })
    expect(searchLimits({ ...on, limits: { ...on.limits, resolve_max_terms: 3, q_max_chars: 5 } })).toEqual({ maxTerms: 3, maxTermChars: 5 })
    const noLimits = { ...on } as Partial<GraphStatus>
    delete noLimits.limits
    for (const status of [undefined, noLimits as GraphStatus]) expect(searchLimits(status)).toEqual({ maxTerms: 25, maxTermChars: 200 })
    for (const value of [0, 1.5, '25']) {
      const status = { ...on, limits: { ...on.limits, resolve_max_terms: value, q_max_chars: value } } as unknown as GraphStatus
      expect(searchLimits(status), String(value)).toEqual({ maxTerms: 25, maxTermChars: 200 })
    }
  })

  it('XS-2: terms are cut, trimmed, deduplicated, capped and refused as 7.10 gives them', () => {
    const none = (terms: string[]) => terms.map((term) => ({ term, refused: null }))
    const many = Array.from({ length: 27 }, (_, index) => `T${index + 1}`)
    const cases: [string, ReturnType<typeof splitSearch>][] = [
      ['SYN-TF1, SYN-SHARED\nSYN-NOPE', { entries: none(['SYN-TF1', 'SYN-SHARED', 'SYN-NOPE']), sent: ['SYN-TF1', 'SYN-SHARED', 'SYN-NOPE'], over: 0 }],
      [' SYN-TF1 ,,SYN-TF1\t\n', { entries: none(['SYN-TF1']), sent: ['SYN-TF1'], over: 0 }],
      ['syn-tf1,SYN-TF1', { entries: none(['syn-tf1', 'SYN-TF1']), sent: ['syn-tf1', 'SYN-TF1'], over: 0 }],
      ['a\u0007b,c', { entries: [{ term: 'a\u0007b', refused: 'control' }, { term: 'c', refused: null }], sent: ['c'], over: 0 }],
      ['a\uD800b,c', { entries: [{ term: 'a\uD800b', refused: 'unsendable' }, { term: 'c', refused: null }], sent: ['c'], over: 0 }],
      ['a'.repeat(201), { entries: [{ term: 'a'.repeat(201), refused: 'long' }], sent: [], over: 0 }],
      [many.join(','), { entries: none(many.slice(0, 25)), sent: many.slice(0, 25), over: 2 }],
      ['  ,\n', { entries: [], sent: [], over: 0 }],
      ['a\u0085b', { entries: [{ term: 'a\u0085b', refused: 'control' }], sent: [], over: 0 }],
      ['a'.repeat(200), { entries: none(['a'.repeat(200)]), sent: ['a'.repeat(200)], over: 0 }],
      ['\u{1F600}'.repeat(200), { entries: none(['\u{1F600}'.repeat(200)]), sent: ['\u{1F600}'.repeat(200)], over: 0 }],
      ['a\u000Db,c', { entries: none(['a', 'b', 'c']), sent: ['a', 'b', 'c'], over: 0 }],
      ['a\uD800b,c,\u0007\uDC00', {
        entries: [{ term: 'a\uD800b', refused: 'unsendable' }, { term: 'c', refused: null }, { term: '\u0007\uDC00', refused: 'control' }], sent: ['c'], over: 0,
      }],
      ['a'.repeat(201) + '\uDFFF', { entries: [{ term: 'a'.repeat(201) + '\uDFFF', refused: 'unsendable' }], sent: [], over: 0 }],
    ]
    expect('\u{1F600}'.repeat(200)).toHaveLength(400)
    for (const [text, result] of cases) expect(splitSearch(text, LIMITS), JSON.stringify(text).slice(0, 40)).toEqual(result)
  })

  it('XS-3: a chunk reads as searching, failed, refused or answered', () => {
    expect(chunkOutcome({ data: undefined, error: null, isFetching: true, size: 1 })).toEqual({ kind: 'searching' })
    const error = answered(500, { detail: 'x' })
    expect(chunkOutcome({ data: undefined, error, isFetching: false, size: 1 })).toEqual({ kind: 'error', error })
    const shared = resolveBody('resolve-tf1-shared')
    const release2 = shared.releases[0]
    expect(shared.releases.map((release) => [release.id, release.is_synthetic])).toEqual([['release:2', true]])
    const answer = { kind: 'answered', results: shared.results, cut: 0, releases: shared.releases }
    expect(chunkOutcome({ data: shared, error: null, isFetching: false, size: 2 })).toEqual(answer)
    expect(chunkOutcome({ data: shared, error: null, isFetching: true, size: 2 })).toEqual(answer)
    expect(chunkOutcome({ data: cutResolve, error: null, isFetching: false, size: 1 })).toMatchObject({ kind: 'answered', cut: 1 })
    for (const terms of [-1, '1']) {
      const body = { ...cutResolve, page: { ...cutResolve.page, omitted: { terms } } }
      expect(chunkOutcome({ data: body, error: null, isFetching: false, size: 1 }), String(terms)).toMatchObject({ kind: 'answered', cut: 0 })
    }
    const listed = { ...shared, releases: [null, 'x', { id: 'release:x' }, { ...release2, is_synthetic: 'yes' }, release2] }
    expect(chunkOutcome({ data: listed, error: null, isFetching: false, size: 2 })).toMatchObject({ releases: [release2] })
    const noReleases: Partial<GraphResolveResponse> = { ...shared }
    delete noReleases.releases
    for (const data of [{ ...shared, releases: null }, noReleases]) {
      expect(chunkOutcome({ data, error: null, isFetching: false, size: 2 })).toMatchObject({ kind: 'answered', releases: [] })
    }
    const bent = (change: (body: GraphResolveResponse) => void) => {
      const body = copy(shared)
      change(body)
      return body
    }
    const refused: [unknown, number][] = [
      [{}, 1],
      [{ results: [] }, 1],
      [shared, 3],
      [bent((body) => { delete (body.results[0].candidates[0] as Partial<GraphCandidate>).entity }), 2],
      [bent((body) => { (body.results[0].candidates[0].entity as unknown as { id: number }).id = 7 }), 2],
      [bent((body) => { (body.results[0].candidates[0].entity as unknown as { label: null }).label = null }), 2],
      [bent((body) => { (body.results[0].candidates[0].entity as unknown as { type: number }).type = 7 }), 2],
      [bent((body) => { body.results[0].candidates[0].entity.primary_identifier = { namespace: 'synthetic_gene', value: null as unknown as string } }), 2],
      [bent((body) => { (body.results[1].candidates as unknown[]).push(null) }), 2],
    ]
    for (const [data, size] of refused) expect(chunkOutcome({ data, error: null, isFetching: false, size })).toEqual({ kind: 'error', error: null })
  })

  it("XS-4: each term's outcome comes from its chunk by position", () => {
    const split = splitSearch('SYN-TF1,SYN-SHARED,a\u0007b,SYN-ALPHA,syn-tf1', LIMITS)
    const chunks = [['SYN-TF1', 'SYN-SHARED'], ['SYN-ALPHA', 'syn-tf1']]
    const shared = resolveBody('resolve-tf1-shared')
    const alpha = resolveBody('resolve-alpha-variant')
    const results = termOutcomes(split, chunks, outcomesOf([shared, alpha], [2, 2]))
    expect(results.map((result) => [result.entry.term, result.outcome, result.chunk])).toEqual([
      ['SYN-TF1', { kind: 'resolved', candidate: shared.results[0].candidates[0] }, 0],
      ['SYN-SHARED', { kind: 'choose', status: 'ambiguous', candidates: shared.results[1].candidates, truncated: false }, 0],
      ['a\u0007b', { kind: 'refused', reason: 'control' }, null],
      ['SYN-ALPHA', { kind: 'choose', status: 'candidate', candidates: alpha.results[0].candidates, truncated: false }, 1],
      ['syn-tf1', { kind: 'choose', status: 'candidate', candidates: alpha.results[1].candidates, truncated: false }, 1],
    ])
    expect(shared.results[0].candidates[0].entity.id).toBe('entity:7')
    const e = answered(500, { detail: 'x' })
    expect(termOutcomes(split, chunks, [{ kind: 'searching' }, { kind: 'error', error: e }]).map((result) => result.outcome)).toEqual([
      { kind: 'searching' }, { kind: 'searching' }, { kind: 'refused', reason: 'control' }, { kind: 'failed', error: e }, { kind: 'failed', error: e },
    ])
    const one = termOutcomes(split, [['SYN-TF1']], [{ kind: 'searching' }])
    expect(one.filter((result) => result.entry.refused === null).slice(1).map((result) => result.outcome))
      .toEqual([{ kind: 'failed', error: null }, { kind: 'failed', error: null }, { kind: 'failed', error: null }])
    const two = copy(shared)
    two.results[0].candidates.push(copy(shared.results[1].candidates[0]))
    const [first] = termOutcomes(splitSearch('SYN-TF1,SYN-SHARED', LIMITS), [['SYN-TF1', 'SYN-SHARED']], outcomesOf([two], [2]))
    expect(first.outcome).toEqual({ kind: 'choose', status: 'resolved', candidates: two.results[0].candidates, truncated: false })
    expect(searchWords.termLineText('SYN-TF1', first.outcome, context())).toBe(ambiguousText('SYN-TF1'))
    expect(autoAdds([first.outcome], [], 10)).toEqual([])
  })

  it('XS-5: resolved terms add their entities in typed order up to the limit', () => {
    const split = splitSearch(MANY_SEARCH_TERMS.join(','), LIMITS)
    const outcomes = termOutcomes(split, [[...MANY_SEARCH_TERMS]], outcomesOf([manyTermResolve], [25])).map((result) => result.outcome)
    const ids = (nodes: GraphNode[]) => nodes.map((node) => node.id)
    const range = (from: number, to: number) => Array.from({ length: to - from + 1 }, (_, index) => `entity:${from + index}`)
    expect(ids(autoAdds(outcomes, [], 10))).toEqual(range(910, 919))
    expect(ids(autoAdds(outcomes, ['entity:1', 'entity:2', 'entity:3'], 10))).toEqual(range(910, 916))
    expect(ids(autoAdds(outcomes, ['entity:910'], 10))).toEqual(range(911, 919))
    const tf1 = resolveBody('resolve-tf1').results[0].candidates[0]
    const resolved: TermOutcome = { kind: 'resolved', candidate: tf1 }
    expect(ids(autoAdds([resolved, resolved], [], 10))).toEqual(['entity:7'])
    const shared = resolveBody('resolve-tf1-shared').results[1].candidates
    for (const outcome of [{ kind: 'choose', status: 'candidate', candidates: [tf1], truncated: false }, { kind: 'choose', status: 'ambiguous', candidates: shared, truncated: false }, { kind: 'unmatched' }] as TermOutcome[]) {
      expect(autoAdds([outcome], [], 10)).toEqual([])
    }
  })

  it('XS-6: a failure offers a retry only where a repeat can change it', () => {
    const retried: unknown[] = [null, answered(500, { detail: 'x' }), networkFailure(), timedOut(), answered(429, { detail: 't' })]
    const kept: unknown[] = [
      answered(404, { detail: 'Not found.' }), answered(400, { detail: 'x', errors: [{ parameter: 'q', value: 'x', allowed: null, limit: null }] }),
      answered(401, { detail: 'x' }), answered(406, { detail: 'x' }), answered(503, { detail: 'x' }), answered(414, '<html></html>'),
      new GraphRequestTooLongError(5000, 4094), new URIError('URI malformed'),
    ]
    retried.forEach((error, index) => expect(searchMayRetry(error), `retried ${index}`).toBe(true))
    kept.forEach((error, index) => expect(searchMayRetry(error), `kept ${index}`).toBe(false))
  })

  it("XS-7: the type and organism options, and each candidate's organism and match", () => {
    expect(entityTypeOptions(vocab)).toEqual([
      { value: 'gene', text: 'Gene' }, { value: 'protein', text: 'Protein (UniProtKB entry)' }, { value: 'protein_isoform', text: 'Protein isoform' },
    ])
    expect(searchTaxonOptions(vocab)).toEqual([
      { value: '7227', text: 'Drosophila melanogaster (taxid 7227)' }, { value: '9606', text: 'Homo sapiens (taxid 9606)' },
      { value: '32630', text: 'synthetic construct (taxid 32630)' },
    ])
    const gene = resolveBody('resolve-shared').results[0].candidates[0]
    expect(gene.entity.type).toBe('gene')
    expect(candidateOrganism(gene, vocab)).toBe('synthetic construct (taxid 32630)')
    expect(candidateOrganism({ ...gene, entity: { ...gene.entity, taxon: null } }, vocab)).toBeNull()
    const caseCandidate = resolveBody('resolve-alpha-variant').results[1].candidates[0]
    expect(candidateMatch(caseCandidate)).toBe(caseCandidate.matches[0])
    expect(candidateMatch(caseCandidate)).toMatchObject({ kind: 'primary', case_exact: false })
    expect(candidateMatch({ ...caseCandidate, matches: [] })).toBeNull()
    expect(candidateMatch({ ...caseCandidate, matches: [null, ...caseCandidate.matches] as unknown as GraphMatch[] })).toBeNull()
  })

  it('XS-8: the words of searchWords.ts', () => {
    const constants: [string, string][] = [
      ['SEARCH_FORM_NAME', SEARCH_FORM_NAME], ['TERMS_LABEL', TERMS_LABEL], ['TERMS_HINT', TERMS_HINT], ['ENTITY_TYPE_LABEL', ENTITY_TYPE_LABEL],
      ['ANY_ENTITY_TYPE', ANY_ENTITY_TYPE], ['SEARCH_TAXON_LABEL', SEARCH_TAXON_LABEL], ['SEARCH_ANY_ORGANISM', SEARCH_ANY_ORGANISM],
      ['SEARCH_BUTTON', SEARCH_BUTTON], ['SEARCHING', SEARCHING], ['RESULTS_NAME', RESULTS_NAME], ['NO_TERM_SENT', NO_TERM_SENT],
      ['SEARCH_TIMEOUT', SEARCH_TIMEOUT], ['SEARCH_TOO_LONG', SEARCH_TOO_LONG], ['PREFIX_NOTE', PREFIX_NOTE], ['CHOSEN_MARK', CHOSEN_MARK],
      ['CASE_NOTE', CASE_NOTE], ['VERSION_NOTE', VERSION_NOTE],
    ]
    const exported: Record<string, unknown> = Object.fromEntries(Object.entries(searchWords))
    expect(constants.map(([name]) => exported[name])).toEqual(constants.map(([, value]) => value))
    expect(searchWords.PREFIX_SHAPE.source).toBe('^[A-Za-z][A-Za-z0-9._-]*:.')
    expect(searchWords.MATCH_KIND_WORDS).toEqual(MATCH_KIND_WORDS)
    expect({
      TERMS_FIELD_STYLE: searchWords.TERMS_FIELD_STYLE, TERMS_BOX_STYLE: searchWords.TERMS_BOX_STYLE, RESULT_LINE_STYLE: searchWords.RESULT_LINE_STYLE,
      CANDIDATE_LIST_STYLE: searchWords.CANDIDATE_LIST_STYLE, FACTS_STYLE: searchWords.FACTS_STYLE,
    }).toEqual({
      TERMS_FIELD_STYLE: { display: 'flex', flexDirection: 'column', gap: 4, minWidth: 0, flex: '1 1 320px' },
      TERMS_BOX_STYLE: { ...INPUT_STYLE, width: '100%', boxSizing: 'border-box', resize: 'vertical' },
      RESULT_LINE_STYLE: { margin: '4px 0' },
      CANDIDATE_LIST_STYLE: { marginLeft: 16 },
      FACTS_STYLE: { margin: '0 0 4px', color: 'var(--text-muted)' },
    })
    for (const [got, want] of wordPairs()) expect(got).toBe(want)
  })

  it('XS-9: no search word says what may not be said, and the restated words equal F4\'s', () => {
    const strings = Object.values(searchWords).flatMap((value): string[] => {
      if (typeof value === 'string') return [value]
      if (typeof value === 'object' && value !== null && !(value instanceof RegExp)) return Object.values(value).filter((entry): entry is string => typeof entry === 'string')
      return []
    })
    const outputs = wordPairs().map(([got]) => got)
    expect(outputs.length).toBeGreaterThan(40)
    expect([...strings, ...outputs].filter((value) => value.includes('not enabled') || BANNED.test(value))).toEqual([])
    expect(searchWords.SEARCH_BUTTON).toBe(seedWords.SEED_BUTTON)
    expect(searchWords.SEARCHING).toBe(seedWords.SEARCHING)
    expect(searchWords.SEARCH_TIMEOUT).toBe(seedWords.SEARCH_TIMEOUT)
    expect(searchWords.PREFIX_NOTE).toBe(seedWords.PREFIX_NOTE)
    expect(searchWords.PREFIX_SHAPE.source).toBe(seedWords.PREFIX_SHAPE.source)
    expect(searchWords.CHOSEN_MARK).toBe(seedWords.CHOSEN_MARK)
    expect(searchWords.limitText(1)).toBe(seedWords.limitText(1))
    expect(searchWords.limitText(10)).toBe(seedWords.limitText(10))
    expect(searchWords.candidateText('syn-tf1')).toBe(seedWords.candidateText('syn-tf1'))
    expect(searchWords.ambiguousText('SYN-SHARED')).toBe(seedWords.ambiguousText('SYN-SHARED'))
    expect(searchWords.chosenText('SYN-TF1 (Gene)')).toBe(seedWords.chosenText('SYN-TF1 (Gene)'))
    expect(searchWords.truncatedText(20)).toBe(seedWords.truncatedText(20))
    expect(searchWords.addText('SYN-TF1 (Gene)')).toBe(seedWords.addText('SYN-TF1 (Gene)'))
  })

  it('XS-10: a submit sends one request, adds the resolved entity once and offers the ambiguous ones', async () => {
    const user = userEvent.setup()
    const view = renderSearch()
    const form = screen.getByRole('search', { name: SEARCH_FORM_NAME })
    const terms = within(form).getByRole('textbox', { name: TERMS_LABEL })
    expect(terms.tagName).toBe('TEXTAREA')
    expect(terms).toHaveAccessibleDescription(TERMS_HINT)
    const type = within(form).getByRole('combobox', { name: ENTITY_TYPE_LABEL }) as HTMLSelectElement
    expect(Array.from(type.options, (option) => option.textContent)).toEqual([ANY_ENTITY_TYPE, 'Gene', 'Protein (UniProtKB entry)', 'Protein isoform'])
    const taxon = within(form).getByRole('combobox', { name: SEARCH_TAXON_LABEL }) as HTMLSelectElement
    expect(Array.from(taxon.options, (option) => option.textContent))
      .toEqual([SEARCH_ANY_ORGANISM, 'Drosophila melanogaster (taxid 7227)', 'Homo sapiens (taxid 9606)', 'synthetic construct (taxid 32630)'])
    const button = within(form).getByRole('button', { name: SEARCH_BUTTON })
    expect(line().textContent).toBe('')
    expect(screen.queryByRole('list', { name: RESULTS_NAME })).toBeNull()
    await user.click(box())
    await user.paste('SYN-TF1, SYN-SHARED')
    await pause(300)
    expect(asked()).toEqual([])
    const answer = held()
    server.use(http.get(RESOLVE, async () => {
      await answer.promise
      return undefined
    }))
    await user.click(button)
    await lineIs(SEARCHING)
    expect(lines()).toEqual([termSearchingText('SYN-TF1'), termSearchingText('SYN-SHARED')])
    expect(view.onAdd).not.toHaveBeenCalled()
    expect(document.activeElement).toBe(button)
    answer.release()
    await lineIs('2 terms searched. 1 is being added; 1 needs a choice from its list below.')
    expect(asked()).toEqual([RESOLVE_TF1_SHARED])
    const shared = resolveBody('resolve-tf1-shared')
    expect(view.onAdd).toHaveBeenCalledTimes(1)
    expect(view.onAdd).toHaveBeenLastCalledWith([shared.results[0].candidates[0].entity])
    expect(lines()).toEqual([resolvedAddingText('SYN-TF1', 'SYN-TF1 (Gene)'), ambiguousText('SYN-SHARED')])
    const list = screen.getByRole('list', { name: matchesName('SYN-SHARED') })
    const adds = within(list).getAllByRole('button')
    expect(adds.map((node) => node.textContent)).toEqual([addText('SYN-SHARED (Gene)'), addText('SYN-SHARED (Protein (UniProtKB entry))')])
    for (const add of adds) expect(add.parentElement?.querySelector('p')?.textContent).toBe(SYNTHETIC_FACTS)
    const results = screen.getByRole('list', { name: RESULTS_NAME })
    const between = badgeParagraphs().filter((node) => following(line(), node) && following(node, results))
    expect(between).toHaveLength(1)
    expect(badgeParagraphs()).toHaveLength(1)
    expect(between[0].firstElementChild?.textContent).toBe(SYNTHETIC_BADGE)

    view.again({ chosen: ['entity:7'] })
    expect(lines()[0]).toBe(resolvedChosenText('SYN-TF1', 'SYN-TF1 (Gene)'))
    expect(line().textContent).toBe('2 terms searched. 1 is among the chosen entities; 1 needs a choice from its list below.')
    expect(view.onAdd).toHaveBeenCalledTimes(1)
    view.again({ chosen: ['entity:7'], badged: true })
    expect(badgeParagraphs()).toEqual([])
    view.again({ chosen: ['entity:7'] })
    expect(badgeParagraphs()).toHaveLength(1)
    const first = within(screen.getByRole('list', { name: matchesName('SYN-SHARED') })).getAllByRole('button')[0]
    await user.click(first)
    expect(view.onAdd).toHaveBeenCalledTimes(2)
    expect(view.onAdd).toHaveBeenLastCalledWith([shared.results[1].candidates[0].entity])
    expect(shared.results[1].candidates[0].entity.id).toBe('entity:6')
    expect(document.activeElement).toBe(first)
    view.again({ chosen: ['entity:7', 'entity:6'] })
    const picked = within(screen.getByRole('list', { name: matchesName('SYN-SHARED') })).getAllByRole('button')[0]
    expect(picked).toHaveAttribute('aria-disabled', 'true')
    expect(picked).toBeEnabled()
    expect(picked.style.borderStyle).toBe('dashed')
    expect(picked.nextSibling?.textContent).toBe(CHOSEN_MARK)
    await user.click(picked)
    expect(view.onAdd).toHaveBeenCalledTimes(2)
    expect(lines()[1]).toBe(`${ambiguousText('SYN-SHARED')} ${chosenText('SYN-SHARED (Gene)')}`)
    view.again({ chosen: [] })
    expect(lines()[0]).toBe(resolvedNotChosenText('SYN-TF1', 'SYN-TF1 (Gene)'))
    expect(line().textContent).toBe('2 terms searched. 1 needs a choice from its list below.')
    await user.click(screen.getByRole('button', { name: SEARCH_BUTTON }))
    await waitFor(() => expect(view.onAdd).toHaveBeenCalledTimes(3), GRAPH_WAIT)
    expect(view.onAdd).toHaveBeenLastCalledWith([shared.results[0].candidates[0].entity])
    expect(asked()).toEqual([RESOLVE_TF1_SHARED])
  }, 20_000)

  it('XS-11: refused terms are never sent, and both caps are worded from status', async () => {
    const user = userEvent.setup()
    renderSearch()
    await searchFor(user, '')
    await pause(300)
    expect(line().textContent).toBe('')
    await searchFor(user, '  ,\n')
    await pause(300)
    expect(line().textContent).toBe('')
    expect(asked()).toEqual([])
    await searchFor(user, 'a\u0007b, SYN-NOPE')
    await lineIs('1 term searched. 1 matched no entity available to you.')
    expect(asked()).toEqual([RESOLVE_NOPE])
    expect(lines()).toEqual([controlTermText('a\u0007b'), unmatchedText('SYN-NOPE', null, false)])
    await searchFor(user, 'a'.repeat(201))
    await pause(300)
    expect(asked()).toEqual([RESOLVE_NOPE])
    expect(lines()).toEqual([longTermText('a'.repeat(201), 200)])
    expect(line().textContent).toBe(NO_TERM_SENT)
    server.use(http.get(RESOLVE, () => HttpResponse.json(makeResolveResponse(['Ras:GTP complex']))))
    await searchFor(user, 'Ras:GTP complex')
    await waitFor(() => expect(asked()).toEqual([RESOLVE_NOPE, '/api/graph/v1/resolve?q=Ras:GTP+complex']), GRAPH_WAIT)
    await waitFor(() => expect(lines()).toEqual([`${unmatchedText('Ras:GTP complex', null, false)} ${PREFIX_NOTE}`]), GRAPH_WAIT)
    cleanup()
    const on = statusOn()
    serveOwnTerms()
    renderSearch({ client: clientWith({ ...on, limits: { ...on.limits, resolve_max_terms: 3, q_max_chars: 5 } }) })
    const before = asked().length
    await searchFor(user, 'SYN-A, abcdef, SYN-B, SYN-C, SYN-D')
    await lineIs('2 terms searched. 2 matched no entity available to you. 2 more terms were not searched: a search takes at most 3 terms.')
    expect(asked().slice(before)).toEqual(['/api/graph/v1/resolve?q=SYN-A,SYN-B'])
    expect(lines()).toEqual([unmatchedText('SYN-A', null, false), longTermText('abcdef', 5), unmatchedText('SYN-B', null, false)])
  }, 20_000)

  it("XS-12: the search's own organism and type, and the view's releases or browser, reach the request", async () => {
    const user = userEvent.setup()
    server.use(http.get(RESOLVE, ({ request }) => {
      const q = new URL(request.url).searchParams.get('q')
      return HttpResponse.json(resolveBody(q === 'SYN-NOPE' ? 'resolve-unmatched' : 'resolve-tf1-shared'))
    }))
    renderSearch()
    await user.selectOptions(screen.getByRole('combobox', { name: SEARCH_TAXON_LABEL }), 'synthetic construct (taxid 32630)')
    await user.selectOptions(screen.getByRole('combobox', { name: ENTITY_TYPE_LABEL }), 'Gene')
    await searchFor(user, 'SYN-NOPE')
    await waitFor(() => expect(lines()).toEqual([unmatchedText('SYN-NOPE', 'synthetic construct', false)]), GRAPH_WAIT)
    expect(asked()).toEqual(['/api/graph/v1/resolve?q=SYN-NOPE&taxon=32630&entity_type=gene'])
    await user.selectOptions(screen.getByRole('combobox', { name: ENTITY_TYPE_LABEL }), ANY_ENTITY_TYPE)
    await pause(300)
    expect(asked()).toEqual(['/api/graph/v1/resolve?q=SYN-NOPE&taxon=32630&entity_type=gene'])
    cleanup()
    let before = asked().length
    renderSearch({ taxon: 32630, entityType: 'gene' })
    expect(screen.queryByRole('combobox', { name: SEARCH_TAXON_LABEL })).toBeNull()
    expect(screen.queryByRole('combobox', { name: ENTITY_TYPE_LABEL })).toBeNull()
    await searchFor(user, 'SYN-NOPE')
    await waitFor(() => expect(asked().slice(before)).toEqual(['/api/graph/v1/resolve?q=SYN-NOPE&taxon=32630&entity_type=gene']), GRAPH_WAIT)
    await waitFor(() => expect(lines()).toHaveLength(1), GRAPH_WAIT)
    const cases: [EntitySearchProps['selection'], string, string, string][] = [
      [{ releases: ['release:1'] }, 'SYN-TF1, SYN-SHARED', '/api/graph/v1/resolve?q=SYN-TF1,SYN-SHARED&releases=release:1', ambiguousText('SYN-SHARED')],
      [{ browser: 'synthetic-regulation' }, 'SYN-TF1, SYN-SHARED', '/api/graph/v1/resolve?q=SYN-TF1,SYN-SHARED&browser=synthetic-regulation', ambiguousText('SYN-SHARED')],
      [{ releases: ['release:1'] }, 'SYN-NOPE', '/api/graph/v1/resolve?q=SYN-NOPE&releases=release:1', unmatchedText('SYN-NOPE', null, true)],
    ]
    for (const [selection, text, request, last] of cases) {
      cleanup()
      before = asked().length
      renderSearch({ selection })
      await searchFor(user, text)
      await waitFor(() => expect(lines()[lines().length - 1]).toBe(last), GRAPH_WAIT)
      expect(asked().slice(before)).toEqual([request])
    }
    expect(asked().filter((url) => /taxon|entity_type/.test(url))).toEqual([
      '/api/graph/v1/resolve?q=SYN-NOPE&taxon=32630&entity_type=gene', '/api/graph/v1/resolve?q=SYN-NOPE&taxon=32630&entity_type=gene',
    ])
  }, 20_000)

  it('XS-13: a long search goes in chunks, and a failed chunk is worded and retried alone', async () => {
    const user = userEvent.setup()
    // 5 terms of 720 bytes, four commas and the 37 bytes of the request line around them: 3,641; six: 4,362 (F1's 4,094-byte line).
    expect(encodeURIComponent(GREEK[0]).length).toBe(720)
    expect(5 * 720 + 4 + 37).toBe(3641)
    expect(6 * 720 + 5 + 37).toBe(4362)
    expect(chunkResolveTerms({ q: GREEK }, 25)).toEqual(SLICES)
    let refused = 0
    serveOwnTerms((terms) => {
      if (terms[0] === GREEK[10] && refused === 0) {
        refused += 1
        return HttpResponse.json({ detail: 't' }, { status: 429, headers: { 'Retry-After': '7' } })
      }
      return undefined
    })
    const view = renderSearch()
    await searchFor(user, GREEK.join(','))
    await lineIs(`20 terms searched. 20 matched no entity available to you. 5 terms could not be searched. ${THROTTLED_SEVEN}`)
    expect(resolveAsked()).toHaveLength(5)
    expect(qsOf(resolveAsked()).sort()).toEqual(SLICES.map((slice) => slice.join(',')).sort())
    expect(view.onAdd).not.toHaveBeenCalled()
    expect(lines()).toEqual(GREEK.map((term, index) => (index >= 10 && index < 15 ? termFailedText(term) : unmatchedText(term, null, false))))
    await user.click(screen.getByRole('button', { name: TRY_AGAIN }))
    await lineIs('25 terms searched. 25 matched no entity available to you.')
    expect(resolveAsked()).toHaveLength(6)
    expect(qsOf(resolveAsked().slice(5))).toEqual([SLICES[2].join(',')])
    expect(lines()).toEqual(GREEK.map((term) => unmatchedText(term, null, false)))
    expect(screen.queryByRole('button', { name: TRY_AGAIN })).toBeNull()
    await waitFor(() => expect(document.activeElement).toBe(line()), GRAPH_WAIT)
    expect(view.onAdd).not.toHaveBeenCalled()
    cleanup()
    server.use(http.get(RESOLVE, () => HttpResponse.json({ results: [] })))
    renderSearch()
    await searchFor(user, 'SYN-TF1')
    await lineIs(`${NO_TERM_SENT} ${failedText(1)}`)
    expect(lines()).toEqual([termFailedText('SYN-TF1')])
    expect(screen.getByRole('button', { name: TRY_AGAIN })).toBeInTheDocument()
    cleanup()
    server.use(http.get(RESOLVE, () => HttpResponse.json({ detail: 'x', errors: [] }, { status: 400 })))
    renderSearch()
    await searchFor(user, 'SYN-TF1')
    await lineIs(`${NO_TERM_SENT} ${failedText(1)}`)
    expect(lines()).toEqual([termFailedText('SYN-TF1')])
    expect(screen.queryByRole('button', { name: TRY_AGAIN })).toBeNull()
  }, 20_000)

  it("XS-14: the API's cut is worded in the search's own line", async () => {
    const user = userEvent.setup()
    server.use(http.get(RESOLVE, () => HttpResponse.json(cutResolve)))
    renderSearch()
    await searchFor(user, 'SYN-CUT')
    await lineIs(`1 term searched. 1 needs a choice from its list below. ${cutText(1)}`)
    expect(lines()).toEqual([`${ambiguousText('SYN-CUT')} ${truncatedText(20)}`])
    const adds = within(screen.getByRole('list', { name: matchesName('SYN-CUT') })).getAllByRole('button')
    expect(adds).toHaveLength(20)
    expect(adds[0].textContent).toBe(addText('SYN-CUT (Gene) (entity:1001)'))
  }, 20_000)

  it('XS-15: past the seed limit the rest are worded, never added', async () => {
    const user = userEvent.setup()
    server.use(http.get(RESOLVE, () => HttpResponse.json(manyTermResolve)))
    const view = renderSearch()
    await searchFor(user, MANY_SEARCH_TERMS.join(','))
    await waitFor(() => expect(view.onAdd).toHaveBeenCalledTimes(1), GRAPH_WAIT)
    expect(asked()).toEqual([`/api/graph/v1/resolve?q=${MANY_SEARCH_TERMS.join(',')}`])
    const ten = Array.from({ length: 10 }, (_, index) => `entity:${910 + index}`)
    expect((view.onAdd.mock.calls[0][0] as GraphNode[]).map((node) => node.id)).toEqual(ten)
    view.again({ chosen: ten })
    const all = lines()
    expect(all[0]).toBe(resolvedChosenText('SYN-M01', 'SYN-M01 (Gene)'))
    expect(all[10]).toBe(resolvedLimitText('SYN-M11', 'SYN-M11 (Gene)', 10))
    expect(all[11]).toBe(`${candidateText('SYN-M12')} ${limitText(10)}`)
    expect(all[12]).toBe(`${ambiguousText('SYN-M13')} ${limitText(10)}`)
    expect(all[13]).toBe(unmatchedText('SYN-M14', null, false))
    const m12 = within(screen.getByRole('list', { name: matchesName('SYN-M12') })).getAllByRole('button')
    expect(m12.map((node) => node.textContent)).toEqual([addText('SYN-M12 (Gene)')])
    const m13 = within(screen.getByRole('list', { name: matchesName('SYN-M13') })).getAllByRole('button')
    expect(m13.map((node) => node.textContent)).toEqual([addText('SYN-M13 (Gene) (entity:922)'), addText('SYN-M13 (Gene) (entity:923)')])
    for (const button of [...m12, ...m13]) expect(button).toHaveAttribute('aria-disabled', 'true')
    expect(line().textContent).toBe('25 terms searched. 10 are among the chosen entities; 1 was not added: the network takes no more; 2 need a choice from their lists below; 12 matched no entity available to you.')
    for (const button of [...m12, ...m13]) await user.click(button)
    expect(view.onAdd).toHaveBeenCalledTimes(1)
  }, 20_000)

  it('XS-16: a chunk that has added its entities fails a background refetch; after a removal and Try again, no onAdd call follows', async () => {
    const user = userEvent.setup()
    const resolvedTo = (id: string, label: string) => ({
      status: 'resolved' as const, truncated: false,
      candidates: [makeResolveCandidate({ entity: makeGraphNode({ id, label, primary_identifier: { namespace: 'synthetic_gene', value: label } }) })],
    })
    const results = { [GREEK[0]]: resolvedTo('entity:2000', 'SYN-N2000'), [GREEK[11]]: resolvedTo('entity:2011', 'SYN-N2011') }
    let failing = false
    let failFirstOfThird = 0
    serveOwnTerms((terms) => {
      if (failing && terms[0] === GREEK[0]) return HttpResponse.json({ detail: 'x' }, { status: 500 })
      if (failFirstOfThird === 1 && terms[0] === GREEK[10]) {
        failFirstOfThird = 2
        return HttpResponse.json({ detail: 'x' }, { status: 500 })
      }
      return makeResolveResponse(terms, results)
    })
    const view = renderSearch()
    await searchFor(user, GREEK.join(','))
    await waitFor(() => expect(view.onAdd).toHaveBeenCalledTimes(1), GRAPH_WAIT)
    expect(resolveAsked()).toHaveLength(5)
    expect((view.onAdd.mock.calls[0][0] as GraphNode[]).map((node) => node.id)).toEqual(['entity:2000', 'entity:2011'])
    view.again({ chosen: ['entity:2000', 'entity:2011'] })
    view.again({ chosen: ['entity:2011'] })
    failing = true
    await act(() => view.client.refetchQueries({ queryKey: ['graph', 'resolve'] }))
    await lineIs('20 terms searched. 1 is among the chosen entities; 19 matched no entity available to you. 5 terms could not be searched.')
    expect(resolveAsked()).toHaveLength(10)
    expect(screen.getByRole('button', { name: TRY_AGAIN })).toBeInTheDocument()
    failing = false
    await user.click(screen.getByRole('button', { name: TRY_AGAIN }))
    await lineIs('25 terms searched. 1 is among the chosen entities; 23 matched no entity available to you.')
    expect(resolveAsked()).toHaveLength(11)
    expect(qsOf(resolveAsked().slice(10))).toEqual([SLICES[0].join(',')])
    expect(lines()[0]).toBe(resolvedNotChosenText(GREEK[0], 'SYN-N2000 (Gene)'))
    expect(view.onAdd).toHaveBeenCalledTimes(1)
    cleanup()
    failFirstOfThird = 1
    const second = renderSearch({ client: clientWith() })
    await searchFor(user, GREEK.join(','))
    await waitFor(() => expect(second.onAdd).toHaveBeenCalledTimes(1), GRAPH_WAIT)
    expect((second.onAdd.mock.calls[0][0] as GraphNode[]).map((node) => node.id)).toEqual(['entity:2000'])
    second.again({ chosen: ['entity:2000'] })
    await user.click(await screen.findByRole('button', { name: TRY_AGAIN }, GRAPH_WAIT))
    await waitFor(() => expect(second.onAdd).toHaveBeenCalledTimes(2), GRAPH_WAIT)
    expect((second.onAdd.mock.calls[1][0] as GraphNode[]).map((node) => node.id)).toEqual(['entity:2011'])
  }, 20_000)
})

