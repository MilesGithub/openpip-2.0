import { AxiosError } from 'axios'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { apiClient } from '../../api/client'
import { GraphBadBodyError, GraphRequestTooLongError } from '../../api/graph/errors'
import { normalizeResolveParams, resolveWire } from '../../api/graph/params'
import type { GraphResolveParams, GraphSelection } from '../../api/graph/params'
import { GRAPH_PARAMS_SERIALIZER } from '../../api/graph/request'
import { makeGraphRelease, namespaceAliasResolve, namespaceAmbiguousResolve } from '../../mocks/fixtures/typedGraph/extras'
import { recorded } from '../../test/graphHooks'
import { seedsFromSearch } from '../browsers/instanceAddress'
import * as seedWords from '../browsers/seedWords'
import type { GraphCandidate, GraphResolveResponse, GraphVocabularies } from '../graph/types'
import { EXPLORE_LIMIT_FALLBACKS, exploreValueValid } from '../graph/viewState'
import { isolateText } from '../graph/words/isolateText'
import {
  ENTITIES_PATH, ENTITY_SELECTION_NAMES, entityHref, entityLookupHref, entitySelectionFromSearch, entitySelectionSearch, isEntityRef,
} from './entityAddress'
import * as entityWords from './entityWords'
import { lookupOutcome, lookupParams, lookupRefusal, lookupState, namespaceLabel } from './lookupModel'
import type { LookupInput, LookupOutcome, LookupState } from './lookupModel'

// X1 spec 12.9 (XE): pure; 12.0 item 2's hooks.
beforeEach(() => {
  vi.spyOn(console, 'warn')
  vi.spyOn(console, 'error')
})

afterEach(() => {
  // Nothing renders here, so there is nothing to unmount before the calls are read.
  const calls = [...vi.mocked(console.warn).mock.calls, ...vi.mocked(console.error).mock.calls]
  vi.restoreAllMocks()
  expect(calls.map((args) => args.map(String).join(' ').slice(0, 300))).toEqual([])
})

// Words copied from the spec's section 10.10, never from the module under test.
const ENTITY_ADDRESS_PROBLEM = "This address names no entity: an entity page's address ends with entity: and a number, such as entity:7."
const EXPLORE_ENTITY = 'Explore this entity in the unified network'
const SELECTION_NOUNS = { releases: 'releases', browser: 'browser' }
const LOOKUP_SEARCHING = 'Finding the entity…'
const LOOKUP_CANDIDATES_NAME = 'Matching entities'
const EMPTY_VALUE = 'This address holds no identifier to look up.'
const NAMESPACE_PROBLEM = 'This portal uses no identifier namespace of that name.'
const VALUE_PROBLEM = 'The server refused to look up this identifier.'
const LOOKUP_PROBLEM = 'The server refused this address.'
const LOOKUP_ERROR = 'This identifier could not be looked up.'
const LOOKUP_TIMEOUT = 'The answer for this identifier did not arrive in time.'
const v = (value: string) => isolateText(value)
const lookupCandidateText = (value: string) =>
  `“${v(value)}” matches one entity, though not as its primary identifier is written. Open it if it is the one you meant.`
const lookupAmbiguousText = (value: string) => `“${v(value)}” matches more than one entity. Open the one you meant.`
const lookupTruncatedText = (listed: number) => `More entities match than the ${listed} listed.`
const lookupUnmatchedText = (value: string, label: string | null, scoped: boolean) => {
  const scope = scoped ? ' in the releases this address names' : ''
  return label !== null
    ? `No entity available to you${scope} has the ${label} “${v(value)}”.`
    : `No entity available to you${scope} has the identifier “${v(value)}” in this namespace.`
}
const separatorValueText = (shown: string) =>
  `“${isolateText(shown)}” holds a comma, a tab or a line break, which would make it more than one identifier, so it was not looked up.`
const controlValueText = (shown: string) => `“${isolateText(shown)}” holds a control character, so it was not looked up.`
const longValueText = (shown: string, max: number) => `“${isolateText(shown)}” is longer than ${max === 1 ? '1 character' : `${max} characters`}, so it was not looked up.`
const selectionProblemText = (nouns: string) => `This page cannot use the ${nouns} named in this address.`

const L = EXPLORE_LIMIT_FALLBACKS
const BANNED = /does not exist|not found|undefined|HTTP|No page uses|\b[Nn]one\b/
const vocab = recorded<GraphVocabularies>('vocabularies').body
const resolveBody = (name: string) => recorded<GraphResolveResponse>(name).body
/** As FE/api/graph/params.test.ts:54-56, 65-66. */
const resolveUri = (params: GraphResolveParams) => apiClient.getUri({
  url: '/graph/v1/resolve', params: resolveWire(normalizeResolveParams(params)), paramsSerializer: GRAPH_PARAMS_SERIALIZER,
})
// Errors as F4's VW rows build them (BR/views.test.ts:125-129).
function answered(status: number, data: unknown, headers: Record<string, string> = {}): AxiosError {
  return new AxiosError('x', undefined, undefined, undefined, { status, statusText: '', headers, config: {} as never, data })
}
const networkFailure = () => new AxiosError('x', 'ERR_NETWORK')
const timedOut = () => new AxiosError('x', 'ECONNABORTED')
const outcomeOf = (data: unknown, error: unknown = null) => lookupOutcome({ data, error, isFetching: false, isPaused: false })
const copy = <T>(value: T): T => JSON.parse(JSON.stringify(value)) as T

describe('the entity pages\' address and lookup', () => {
  it('XE-1: the ref grammar, the selection and the two hrefs as 10.3 gives them', () => {
    expect(ENTITIES_PATH).toBe('/entities')
    expect(ENTITY_SELECTION_NAMES).toEqual(['releases', 'browser'])
    for (const value of ['entity:7', 'entity:1234567890123456789']) expect(isEntityRef(value), value).toBe(true)
    for (const value of ['7', 'entity:0', 'entity:01', 'ENTITY:7', 'entity:7 ', 'entity:7,entity:8', 'entity:12345678901234567890']) {
      expect(isEntityRef(value), value).toBe(false)
    }
    const searches: [string, ReturnType<typeof entitySelectionFromSearch>][] = [
      ['', { selection: {}, invalid: [] }],
      ['releases=release%3A2%2Crelease%3A1', { selection: { releases: ['release:1', 'release:2'] }, invalid: [] }],
      ['browser=synthetic-regulation&utm_source=news', { selection: { browser: 'synthetic-regulation' }, invalid: [] }],
      ['releases=release:1&browser=synthetic-regulation', { selection: {}, invalid: ['releases', 'browser'] }],
      ...['releases=release:1&releases=release:2', 'releases=', 'releases=release:1,release:1', 'releases=release:0']
        .map((search): [string, ReturnType<typeof entitySelectionFromSearch>] => [search, { selection: {}, invalid: ['releases'] }]),
      ...['browser=Synthetic-Regulation', 'browser=a&browser=b']
        .map((search): [string, ReturnType<typeof entitySelectionFromSearch>] => [search, { selection: {}, invalid: ['browser'] }]),
    ]
    for (const [search, result] of searches) expect(entitySelectionFromSearch(new URLSearchParams(search), L), search).toEqual(result)
    expect(entitySelectionSearch({ releases: ['release:2', 'release:1', 'release:2'] })).toBe('?releases=release%3A1%2Crelease%3A2')
    expect(entityHref('entity:7')).toBe('/entities/entity%3A7')
    expect(entityHref('entity:7', { releases: ['release:1'] })).toBe('/entities/entity%3A7?releases=release%3A1')
    expect(entityHref('entity:7', { browser: 'synthetic-regulation' })).toBe('/entities/entity%3A7?browser=synthetic-regulation')
    expect(entityLookupHref('synthetic_gene', 'SYN-TF1')).toBe('/entities/synthetic_gene/SYN-TF1')
    expect(entityLookupHref('hgnc', 'HGNC:5')).toBe('/entities/hgnc/HGNC%3A5')
    expect(entityLookupHref('synthetic_gene', 'a/b c', { browser: 'synthetic-regulation' })).toBe('/entities/synthetic_gene/a%2Fb%20c?browser=synthetic-regulation')
    for (const [, { selection }] of searches) {
      expect(entitySelectionFromSearch(new URLSearchParams(entitySelectionSearch(selection)), L)).toEqual({ selection, invalid: [] })
    }
  })

  it('XE-2: the three copies of the ref grammar agree', () => {
    const samples = ['entity:7', 'entity:1234567890123456789', '7', 'entity:0', 'entity:01', 'ENTITY:7', 'entity:7 ', 'entity:12345678901234567890', 'relationship:7', '']
    for (const value of samples) {
      const seeds = seedsFromSearch(new URLSearchParams([['seeds', value]]), 10)
      const f4 = seeds.invalid === false && seeds.seeds.length === 1 && seeds.seeds[0] === value
      expect([isEntityRef(value), exploreValueValid('seeds', value, L)], value).toEqual([f4, f4])
    }
  })

  it('XE-3: a value is refused before sending as 10.7 gives it', () => {
    const cases: [string, number, ReturnType<typeof lookupRefusal>][] = [
      ['SYN-TF1', 200, null], [' SYN-TF1 ', 200, null],
      ['a,b', 200, 'separator'], ['a\tb', 200, 'separator'], ['a\nb', 200, 'separator'], ['a\rb', 200, 'separator'], ['a,\u0007', 200, 'separator'],
      ['a\u0007b', 200, 'control'], ['a\u007Fb', 200, 'control'], ['a\u0085b', 200, 'control'],
      ['', 200, 'empty'], ['   ', 200, 'empty'],
      ['a'.repeat(200), 200, null], ['a'.repeat(201), 200, 'long'], ['é'.repeat(200), 200, null], ['é'.repeat(201), 200, 'long'],
      ['abcd', 3, 'long'],
    ]
    for (const [value, max, refusal] of cases) expect(lookupRefusal(value, max), JSON.stringify(value).slice(0, 40)).toBe(refusal)
  })

  it("XE-4: a lookup sends one term in one namespace with the view's releases or browser", () => {
    const cases: [string, string, GraphSelection, string][] = [
      ['synthetic_gene', 'SYN-TF1', {}, '/api/graph/v1/resolve?q=SYN-TF1&namespace=synthetic_gene'],
      ['synthetic_gene', 'SYN-TF1', { releases: ['release:1'] }, '/api/graph/v1/resolve?q=SYN-TF1&namespace=synthetic_gene&releases=release:1'],
      ['synthetic_gene', 'SYN-TF1', { browser: 'synthetic-regulation' }, '/api/graph/v1/resolve?q=SYN-TF1&namespace=synthetic_gene&browser=synthetic-regulation'],
      ['synthetic_gene', 'a/b c', {}, '/api/graph/v1/resolve?q=a%2Fb+c&namespace=synthetic_gene'],
      ['Synthetic_Gene', 'SYN-TF1', {}, '/api/graph/v1/resolve?q=SYN-TF1&namespace=Synthetic_Gene'],
    ]
    for (const [namespace, value, selection, uri] of cases) {
      const got = resolveUri(lookupParams(namespace, value, selection))
      expect(got).toBe(uri)
      expect(got).not.toMatch(/taxon|entity_type/)
    }
  })

  it('XE-5: a namespace is named by its registry label, compared as written', () => {
    expect(namespaceLabel('synthetic_gene', vocab)).toBe('Synthetic gene ID')
    expect(namespaceLabel('synthetic_protein', vocab)).toBe('Synthetic protein ID')
    expect(namespaceLabel('Synthetic_Gene', vocab)).toBeNull()
    expect(namespaceLabel('no_such_namespace', vocab)).toBeNull()
    expect(namespaceLabel('synthetic_gene', null)).toBeNull()
  })

  it("XE-6: a lookup's answer is followed, offered, worded or refused", () => {
    expect(outcomeOf(resolveBody('resolve-ns-tf1'))).toEqual({ kind: 'resolved', ref: 'entity:7' })
    expect(outcomeOf(resolveBody('resolve-ns-isoform'))).toEqual({ kind: 'resolved', ref: 'entity:11' })
    const caseBody = resolveBody('resolve-ns-tf1-case')
    expect(caseBody.releases.map((release) => [release.id, release.is_synthetic])).toEqual([['release:2', true]])
    expect(outcomeOf(caseBody)).toEqual({
      kind: 'choose', status: 'candidate', candidates: caseBody.results[0].candidates, truncated: false, releases: caseBody.releases,
    })
    expect(caseBody.results[0].candidates).toHaveLength(1)
    expect(outcomeOf(resolveBody('resolve-ns-alpha'))).toEqual({ kind: 'unmatched' })
    const alias = outcomeOf(namespaceAliasResolve)
    expect(alias).toEqual({
      kind: 'choose', status: 'candidate', candidates: namespaceAliasResolve.results[0].candidates, truncated: false, releases: namespaceAliasResolve.releases,
    })
    expect(alias.kind === 'choose' ? alias.candidates.map((candidate) => candidate.entity.id) : []).toEqual(['entity:944'])
    const ambiguous = outcomeOf(namespaceAmbiguousResolve)
    expect(ambiguous).toEqual({
      kind: 'choose', status: 'ambiguous', candidates: namespaceAmbiguousResolve.results[0].candidates, truncated: false,
      releases: namespaceAmbiguousResolve.releases,
    })
    expect(ambiguous.kind === 'choose' ? ambiguous.candidates.map((candidate) => candidate.entity.id) : []).toEqual(['entity:945', 'entity:946'])
    for (const body of [namespaceAliasResolve, namespaceAmbiguousResolve]) {
      expect(body.releases).toEqual([makeGraphRelease()])
      expect([body.releases[0].id, body.releases[0].is_synthetic]).toEqual(['release:901', true])
    }
    expect(outcomeOf(undefined, answered(400, resolveBody('resolve-ns-unknown')))).toEqual({ kind: 'refused', problems: ['namespace'] })
    const problem = (parameter: string) => ({ parameter, value: 'x', allowed: null, limit: null })
    expect(outcomeOf(undefined, answered(400, { detail: 'x', errors: [problem('releases'), problem('releases'), problem('q')] })))
      .toEqual({ kind: 'refused', problems: ['releases', 'q'] })
    const tf1 = resolveBody('resolve-ns-tf1')
    const two = { ...copy(tf1), results: [copy(tf1).results[0], copy(tf1).results[0]] }
    expect(outcomeOf(two)).toEqual({ kind: 'failed', error: null })
    const zero = copy(tf1)
    zero.results[0].candidates[0].entity.id = 'entity:0'
    expect(outcomeOf(zero)).toEqual({ kind: 'failed', error: null })
    const second = copy(tf1)
    second.results[0].candidates.push({ ...copy(tf1).results[0].candidates[0], entity: { ...copy(tf1).results[0].candidates[0].entity, id: 'entity:8' } })
    expect(outcomeOf(second)).toEqual({ kind: 'choose', status: 'resolved', candidates: second.results[0].candidates, truncated: false, releases: second.releases })
    expect(lookupOutcome({ data: undefined, error: null, isFetching: true, isPaused: false })).toEqual({ kind: 'searching' })
    expect(lookupOutcome({ data: undefined, error: null, isFetching: false, isPaused: true })).toEqual({ kind: 'searching' })
    const throttled = answered(429, { detail: 't' }, { 'retry-after': '7' })
    expect(outcomeOf(undefined, throttled)).toEqual({ kind: 'failed', error: throttled })
  })

  it('XE-7: each outcome gives its line, readiness, redirect, removal, retry and badge', () => {
    const blank: LookupState = { line: '', ready: true, candidates: [], redirect: null, removal: [], retry: false, synthetic: false }
    const input = (overrides: Partial<LookupInput>): LookupInput => ({
      value: 'SYN-TF1', label: 'Synthetic gene ID', maxChars: 200, scoped: false, refusal: null, outcome: null, ...overrides,
    })
    const one = namespaceAliasResolve.results[0].candidates
    const twenty: GraphCandidate[] = Array.from({ length: 20 }, (_, index) => ({ ...one[0], entity: { ...one[0].entity, id: `entity:${100 + index}` } }))
    const synthetic = makeGraphRelease()
    const plain = makeGraphRelease({ is_synthetic: false })
    const choose = (status: string, candidates: readonly GraphCandidate[], truncated: boolean, releases = [synthetic]): LookupOutcome =>
      ({ kind: 'choose', status, candidates, truncated, releases })
    const refused = (problems: string[]): LookupOutcome => ({ kind: 'refused', problems })
    const failed = (error: unknown): LookupOutcome => ({ kind: 'failed', error })
    const long = 'a'.repeat(201)
    const cases: [string, LookupInput, LookupState][] = [
      ['separator', input({ value: 'a,b', refusal: 'separator' }), { ...blank, line: separatorValueText('a,b') }],
      ['control', input({ value: 'a\u0007b', refusal: 'control' }), { ...blank, line: controlValueText('a\u0007b') }],
      ['empty', input({ value: '  ', refusal: 'empty' }), { ...blank, line: EMPTY_VALUE }],
      ['long', input({ value: long, refusal: 'long' }), { ...blank, line: longValueText(long, 200) }],
      ['no outcome', input({}), { ...blank, line: LOOKUP_SEARCHING, ready: false }],
      ['searching', input({ outcome: { kind: 'searching' } }), { ...blank, line: LOOKUP_SEARCHING, ready: false }],
      ['resolved', input({ outcome: { kind: 'resolved', ref: 'entity:7' } }), { ...blank, line: LOOKUP_SEARCHING, ready: false, redirect: 'entity:7' }],
      ['candidate', input({ outcome: choose('candidate', one, false) }), { ...blank, line: lookupCandidateText('SYN-TF1'), candidates: one, synthetic: true }],
      ['ambiguous truncated', input({ outcome: choose('ambiguous', twenty, true) }),
        { ...blank, line: `${lookupAmbiguousText('SYN-TF1')} ${lookupTruncatedText(20)}`, candidates: twenty, synthetic: true }],
      ['candidate, no release', input({ outcome: choose('candidate', one, false, []) }), { ...blank, line: lookupCandidateText('SYN-TF1'), candidates: one }],
      ['candidate, a release not synthetic', input({ outcome: choose('candidate', one, false, [plain]) }), { ...blank, line: lookupCandidateText('SYN-TF1'), candidates: one }],
      ['ambiguous, no release', input({ outcome: choose('ambiguous', twenty, true, []) }),
        { ...blank, line: `${lookupAmbiguousText('SYN-TF1')} ${lookupTruncatedText(20)}`, candidates: twenty }],
      ['ambiguous, a release not synthetic', input({ outcome: choose('ambiguous', twenty, true, [plain]) }),
        { ...blank, line: `${lookupAmbiguousText('SYN-TF1')} ${lookupTruncatedText(20)}`, candidates: twenty }],
      ['unmatched', input({ outcome: { kind: 'unmatched' } }), { ...blank, line: lookupUnmatchedText('SYN-TF1', 'Synthetic gene ID', false) }],
      ['unmatched, label null', input({ label: null, outcome: { kind: 'unmatched' } }), { ...blank, line: lookupUnmatchedText('SYN-TF1', null, false) }],
      ['unmatched, scoped', input({ scoped: true, outcome: { kind: 'unmatched' } }), { ...blank, line: lookupUnmatchedText('SYN-TF1', 'Synthetic gene ID', true) }],
      ['refused namespace', input({ outcome: refused(['namespace']) }), { ...blank, line: NAMESPACE_PROBLEM, removal: [] }],
      ['refused releases', input({ outcome: refused(['releases']) }), { ...blank, line: selectionProblemText('releases'), removal: ['releases'] }],
      ['refused namespace and releases', input({ outcome: refused(['namespace', 'releases']) }),
        { ...blank, line: 'This portal uses no identifier namespace of that name. This page cannot use the releases named in this address.', removal: ['releases'] }],
      ['refused q', input({ outcome: refused(['q']) }), { ...blank, line: VALUE_PROBLEM }],
      ['refused x', input({ outcome: refused(['x']) }), { ...blank, line: LOOKUP_PROBLEM }],
      ['refused none', input({ outcome: refused([]) }), { ...blank, line: LOOKUP_PROBLEM }],
      ['failed null', input({ outcome: failed(null) }), { ...blank, line: LOOKUP_ERROR, retry: true }],
      ['a JSON 404', input({ outcome: failed(answered(404, { detail: 'Not found.' })) }), { ...blank, line: LOOKUP_ERROR, retry: true }],
      ['a JSON 500', input({ outcome: failed(answered(500, { detail: 'x' })) }), { ...blank, line: LOOKUP_ERROR, retry: true }],
      ['a bad body', input({ outcome: failed(new GraphBadBodyError(200)) }), { ...blank, line: LOOKUP_ERROR, retry: true }],
      ['a 429', input({ outcome: failed(answered(429, { detail: 't' }, { 'retry-after': '7' })) }),
        { ...blank, line: 'Too many requests. Try again in 7 seconds.', retry: true }],
      ['a network failure', input({ outcome: failed(networkFailure()) }), { ...blank, line: 'The server could not be reached.', retry: true }],
      ['a timeout', input({ outcome: failed(timedOut()) }), { ...blank, line: LOOKUP_TIMEOUT, retry: true }],
      ['a JSON 503', input({ outcome: failed(answered(503, { detail: 'x' })) }), { ...blank, line: LOOKUP_ERROR }],
      ['an HTML 414', input({ outcome: failed(answered(414, '<html></html>')) }), { ...blank, line: LOOKUP_ERROR }],
      ['too long', input({ outcome: failed(new GraphRequestTooLongError(5000, 4094)) }), { ...blank, line: LOOKUP_ERROR }],
    ]
    for (const [name, given, state] of cases) expect(lookupState(given), name).toEqual(state)
  })

  it('XE-8: the words of entityWords.ts', () => {
    expect([
      entityWords.ENTITY_ADDRESS_PROBLEM, entityWords.EXPLORE_ENTITY, entityWords.LOOKUP_SEARCHING, entityWords.LOOKUP_CANDIDATES_NAME,
      entityWords.EMPTY_VALUE, entityWords.NAMESPACE_PROBLEM, entityWords.VALUE_PROBLEM, entityWords.LOOKUP_PROBLEM, entityWords.LOOKUP_ERROR,
      entityWords.LOOKUP_TIMEOUT,
    ]).toEqual([
      ENTITY_ADDRESS_PROBLEM, EXPLORE_ENTITY, LOOKUP_SEARCHING, LOOKUP_CANDIDATES_NAME, EMPTY_VALUE, NAMESPACE_PROBLEM, VALUE_PROBLEM,
      LOOKUP_PROBLEM, LOOKUP_ERROR, LOOKUP_TIMEOUT,
    ])
    expect(entityWords.SELECTION_NOUNS).toEqual(SELECTION_NOUNS)
    const outputs: [string, string][] = [
      // 10.10's examples.
      [entityWords.selectionProblemText(['releases', 'browser']), 'This page cannot use the releases and browser named in this address.'],
      [entityWords.selectionRemovalText(['browser']), 'Show this page without the browser'],
      [entityWords.lookupSubjectText('Synthetic gene ID', 'synthetic_gene', 'SYN-TF1'), `Synthetic gene ID “${isolateText('SYN-TF1')}”`],
      [entityWords.lookupUnmatchedText('SYN-ALPHA', 'Synthetic gene ID', false), `No entity available to you has the Synthetic gene ID “${isolateText('SYN-ALPHA')}”.`],
      [entityWords.lookupUnmatchedText('SYN-ALPHA', 'Synthetic gene ID', true),
        `No entity available to you in the releases this address names has the Synthetic gene ID “${isolateText('SYN-ALPHA')}”.`],
      [entityWords.chooseText('ambiguous', 'SYN-N-SHARED-ALIAS', 20),
        `“${isolateText('SYN-N-SHARED-ALIAS')}” matches more than one entity. Open the one you meant. More entities match than the 20 listed.`],
      [entityWords.lookupProblemText(['namespace', 'releases']),
        'This portal uses no identifier namespace of that name. This page cannot use the releases named in this address.'],
      [entityWords.longValueText('a'.repeat(201), 200), `“${isolateText('a'.repeat(201))}” is longer than 200 characters, so it was not looked up.`],
      // 12.9's own.
      [entityWords.selectionProblemText([]), 'This page cannot use this address.'],
      [entityWords.selectionProblemText(['releases']), 'This page cannot use the releases named in this address.'],
      [entityWords.selectionRemovalText([]), 'Show this page'],
      [entityWords.selectionRemovalText(['releases', 'browser']), 'Show this page without the releases and browser'],
      [entityWords.openText('SYN-TF1 (Gene)'), `Open ${isolateText('SYN-TF1 (Gene)')}`],
      [entityWords.lookupSubjectText(null, 'no_such_namespace', 'SYN-TF1'), `“${isolateText('SYN-TF1')}” in the namespace “${isolateText('no_such_namespace')}”`],
      [entityWords.lookupCandidateText('syn-tf1'),
        `“${isolateText('syn-tf1')}” matches one entity, though not as its primary identifier is written. Open it if it is the one you meant.`],
      [entityWords.lookupTruncatedText(20), 'More entities match than the 20 listed.'],
      [entityWords.chooseText('candidate', 'syn-tf1', null), lookupCandidateText('syn-tf1')],
      [entityWords.chooseText('syn_status', 'x', null), lookupAmbiguousText('x')],
      [entityWords.chooseText('resolved', 'x', null), lookupAmbiguousText('x')],
      [entityWords.lookupUnmatchedText('SYN-ALPHA', null, false), `No entity available to you has the identifier “${isolateText('SYN-ALPHA')}” in this namespace.`],
      [entityWords.lookupUnmatchedText('SYN-ALPHA', null, true),
        `No entity available to you in the releases this address names has the identifier “${isolateText('SYN-ALPHA')}” in this namespace.`],
      [entityWords.separatorValueText('a,b'), separatorValueText('a,b')],
      [entityWords.controlValueText('a\u0007b'), controlValueText('a\u0007b')],
      [entityWords.longValueText('a'.repeat(1001), 200), longValueText(`${'a'.repeat(1000)}… (1 more character not shown)`, 200)],
      [entityWords.lookupProblemText(['q']), VALUE_PROBLEM],
      [entityWords.lookupProblemText(['browser']), 'This page cannot use the browser named in this address.'],
      [entityWords.lookupProblemText(['x']), LOOKUP_PROBLEM],
    ]
    for (const [got, want] of outputs) expect(got).toBe(want)
    expect(entityWords.LOOKUP_CANDIDATES_NAME).toBe(seedWords.CANDIDATES_NAME)
    const strings = [
      ...(Object.values(entityWords) as unknown[]).filter((value): value is string => typeof value === 'string'),
      ...Object.values(entityWords.SELECTION_NOUNS),
      ...outputs.map(([got]) => got),
    ]
    expect(strings.filter((value) => value.includes('not enabled') || BANNED.test(value))).toEqual([])
  })
})
