import { hashKey } from '@tanstack/react-query'
import type { QueryKey } from '@tanstack/react-query'
import { describe, expect, it } from 'vitest'
import { GRAPH_CONTEXT_FACETS } from '../../features/graph/types'
import type { GraphVocabularies } from '../../features/graph/types'
import { demoRefs } from '../../mocks/fixtures/typedGraph/recorded'
import { typedGraphRoutes } from '../../mocks/handlers/typedGraph'
import { recorded } from '../../test/graphHooks'
import { apiClient } from '../client'
import { graphBrowserOptions, graphBrowsersOptions } from './browsers'
import { graphEntityOptions } from './entities'
import { graphMayResend, retryGraphQuery } from './errors'
import { graphNetworkOptions } from './network'
import {
  browsersWire,
  evidenceWire,
  networkWire,
  normalizeBrowsersParams,
  normalizeEvidenceParams,
  normalizeNetworkParams,
  normalizeRelationshipParams,
  normalizeResolveParams,
  normalizeSelection,
  relationshipWire,
  resolveWire,
  selectionWire,
} from './params'
import type {
  GraphBrowsersParams,
  GraphEvidenceParams,
  GraphNetworkParams,
  GraphRelationshipParams,
  GraphResolveParams,
  GraphSelection,
  GraphWireParams,
} from './params'
import { graphEvidenceOptions, graphRelationshipOptions } from './relationships'
import { GRAPH_PARAMS_SERIALIZER } from './request'
import type { GraphViewer } from './request'
import { graphResolveOptions } from './resolve'
import { graphStatusOptions } from './status'
import { graphVocabulariesOptions } from './vocabularies'

const { tf1, tf2, tf3, p2, v1, v2, mixed } = demoRefs

const encoded = (ref: string) => ref.replace(':', '%3A')

const NETWORK_URI = `/api/graph/v1/network?seeds=${tf1}`
const ENTITY_PATH = `/graph/v1/entities/${encoded(tf1)}`
const RELATIONSHIP_PATH = `/graph/v1/relationships/${encoded(mixed)}`
const EVIDENCE_PATH = `${RELATIONSHIP_PATH}/evidence`

function uriOf(url: string, wire: GraphWireParams): string {
  return apiClient.getUri({ url, params: wire, paramsSerializer: GRAPH_PARAMS_SERIALIZER })
}

function expectExact(uri: string, expected: string): void {
  expect(uri).toBe(expected)
  expect(uri).not.toContain('%5B')
  expect(uri).not.toMatch(/=(&|$)/)
}

const networkUri = (params: GraphNetworkParams) =>
  uriOf('/graph/v1/network', networkWire(normalizeNetworkParams(params)))
const resolveUri = (params: GraphResolveParams) =>
  uriOf('/graph/v1/resolve', resolveWire(normalizeResolveParams(params)))
const browsersUri = (params: GraphBrowsersParams) =>
  uriOf('/graph/v1/browsers', browsersWire(normalizeBrowsersParams(params)))

const networkHash = (params: object) => hashKey(graphNetworkOptions('staff', params as GraphNetworkParams).queryKey)
const resolveHash = (params: object) => hashKey(graphResolveOptions('staff', params as GraphResolveParams).queryKey)
const entityHash = (selection?: object) =>
  hashKey(graphEntityOptions('staff', tf1, selection as GraphSelection).queryKey)
const relationshipHash = (filters?: object) =>
  hashKey(graphRelationshipOptions('staff', mixed, filters as GraphRelationshipParams).queryKey)
const evidenceHash = (filters?: object) =>
  hashKey(graphEvidenceOptions('staff', mixed, filters as GraphEvidenceParams).queryKey)
const browsersHash = (filters?: object) =>
  hashKey(graphBrowsersOptions('staff', filters as GraphBrowsersParams).queryKey)

const seeded = { seeds: [tf1] }
const filterLists = ['outcome', 'evidence_basis', 'effect', 'method', 'level', 'score', 'releases']
const filterNames = ['outcome', 'evidence_basis', 'effect', 'method', 'level', 'score', 'context', 'releases', 'browser']
const networkLists = ['layers', ...filterLists]
const networkOptional = [
  'layers',
  'direction',
  ...filterNames,
  'taxon',
  'include_partner_edges',
  'max_edges',
]
const resolveOptional = ['namespace', 'taxon', 'entity_type', 'releases', 'browser']
const selectionNames = ['releases', 'browser']
const browsersOptional = ['q', 'tag', 'relationship_type', 'taxon', 'assay', 'year', 'status', 'availability']

const filterValues: [string, unknown][] = [
  ['outcome', ['supported']],
  ['evidence_basis', ['experimental']],
  ['effect', ['activating']],
  ['method', ['MI:0018']],
  ['level', ['association']],
  ['score', ['a:1:2']],
  ['context', { tissue: 'liver' }],
  ['releases', [v1]],
  ['browser', 's'],
]

describe('the network wire', () => {
  it('sorts the seeds', () => {
    expectExact(networkUri({ seeds: [tf2, tf1] }), `/api/graph/v1/network?seeds=${tf1},${tf2}`)
  })

  it.each<[string, Partial<GraphNetworkParams>, string]>([
    ['layers', { layers: ['b', 'a'] }, 'layers=a,b'],
    ['releases', { releases: [v2, v1, v1] }, `releases=${v1},${v2}`],
    ['outcome all', { outcome: 'all' }, 'outcome=all'],
    ['outcome list', { outcome: ['tested_absent', 'supported'] }, 'outcome=supported,tested_absent'],
    ['score', { score: ['b::3', 'a:1:2'] }, 'score=a:1:2&score=b::3'],
    [
      'context',
      { context: { tissue: 'liver, left lobe', condition: 'pH:7.4' } },
      'context.tissue=liver,+left+lobe&context.condition=pH:7.4',
    ],
    ['taxon 0', { taxon: 0 }, 'taxon=0'],
    [
      'include_partner_edges and max_edges',
      { include_partner_edges: true, max_edges: 500 },
      'include_partner_edges=true&max_edges=500',
    ],
    ['include_partner_edges false', { include_partner_edges: false }, 'include_partner_edges=false'],
    ['browser', { browser: 's' }, 'browser=s'],
    ['direction', { direction: 'out' }, 'direction=out'],
    [
      'evidence_basis',
      { evidence_basis: ['statistical', 'experimental'] },
      'evidence_basis=experimental,statistical',
    ],
    ['effect', { effect: ['not_stated', 'activating'] }, 'effect=activating,not_stated'],
    ['method', { method: ['MI:0407', 'MI:0018'] }, 'method=MI:0018,MI:0407'],
    ['level', { level: ['direct_interaction', 'association'] }, 'level=association,direct_interaction'],
  ])('%s', (_name, extra, expected) => {
    expectExact(networkUri({ ...seeded, ...extra } as GraphNetworkParams), `${NETWORK_URI}&${expected}`)
  })

  it('sorts as plain strings, so a ref is never parsed', () => {
    expectExact(networkUri({ seeds: [tf3, p2] }), `/api/graph/v1/network?seeds=${p2},${tf3}`)
  })

  it('writes context facets in one order, whatever order they came in', () => {
    const one = networkUri({ ...seeded, context: { tissue: 'a', condition: 'b' } })
    const other = networkUri({ ...seeded, context: { condition: 'b', tissue: 'a' } })
    expectExact(one, `${NETWORK_URI}&context.tissue=a&context.condition=b`)
    expect(other).toBe(one)
  })
})

describe('the other builders', () => {
  it.each<[string, Partial<GraphResolveParams>, string]>([
    ['namespace', { namespace: 'n' }, 'namespace=n'],
    ['taxon', { taxon: 32630 }, 'taxon=32630'],
    ['taxon 0', { taxon: 0 }, 'taxon=0'],
    ['entity_type', { entity_type: 'gene' }, 'entity_type=gene'],
    ['releases', { releases: [v1] }, `releases=${v1}`],
    ['browser', { browser: 's' }, 'browser=s'],
  ])('resolve: %s', (_name, extra, expected) => {
    expectExact(resolveUri({ q: ['a'], ...extra } as GraphResolveParams), `/api/graph/v1/resolve?q=a&${expected}`)
  })

  it.each<[string, GraphSelection, string]>([
    ['releases', { releases: [v1] }, `releases=${v1}`],
    ['browser', { browser: 's' }, 'browser=s'],
  ])('entity: %s', (_name, selection, expected) => {
    expectExact(uriOf(ENTITY_PATH, selectionWire(normalizeSelection(selection))), `/api${ENTITY_PATH}?${expected}`)
  })

  it.each<[string, GraphRelationshipParams, string]>([
    ['outcome', { outcome: 'all' }, 'outcome=all'],
    ['releases', { releases: [v1] }, `releases=${v1}`],
    ['browser', { browser: 's' }, 'browser=s'],
  ])('relationship: %s', (_name, filters, expected) => {
    expectExact(
      uriOf(RELATIONSHIP_PATH, relationshipWire(normalizeRelationshipParams(filters))),
      `/api${RELATIONSHIP_PATH}?${expected}`,
    )
  })

  it.each<[string, GraphEvidenceParams, string]>([
    ['outcome', { outcome: 'all' }, 'outcome=all'],
    ['releases', { releases: [v1] }, `releases=${v1}`],
    ['browser', { browser: 's' }, 'browser=s'],
    ['page_size', { page_size: 2 }, 'page_size=2'],
  ])('evidence: %s, on the first page', (_name, filters, expected) => {
    expectExact(
      uriOf(EVIDENCE_PATH, evidenceWire(normalizeEvidenceParams(filters), null)),
      `/api${EVIDENCE_PATH}?${expected}`,
    )
  })

  it('evidence: after, on a later page', () => {
    expectExact(
      uriOf(EVIDENCE_PATH, evidenceWire({ page_size: 1 }, 'evidence:21')),
      `/api${EVIDENCE_PATH}?after=evidence:21&page_size=1`,
    )
  })

  it.each<[string, GraphBrowsersParams, string]>([
    ['q', { q: 'x' }, 'q=x'],
    ['tag', { tag: 't' }, 'tag=t'],
    ['relationship_type', { relationship_type: 'r' }, 'relationship_type=r'],
    ['taxon', { taxon: 5 }, 'taxon=5'],
    ['taxon 0', { taxon: 0 }, 'taxon=0'],
    ['assay', { assay: 'a' }, 'assay=a'],
    ['year', { year: 2020 }, 'year=2020'],
    ['year 0', { year: 0 }, 'year=0'],
    ['status', { status: 'available' }, 'status=available'],
    ['availability', { availability: 'live' }, 'availability=live'],
  ])('browsers: %s', (_name, filters, expected) => {
    expectExact(browsersUri(filters), `/api/graph/v1/browsers?${expected}`)
  })
})

describe('terms and phrases', () => {
  it.each<[string, string[], string]>([
    ['order and duplicates', ['b', 'a', 'b'], 'q=b,a,b'],
    ['a space', ['TP53 isoform'], 'q=TP53+isoform'],
    ['a comma', ['NF-kappa-B, p65'], 'q=NF-kappa-B,+p65'],
  ])('resolve keeps %s', (_name, q, expected) => {
    expectExact(resolveUri({ q }), `/api/graph/v1/resolve?${expected}`)
  })

  it('browsers q is one phrase', () => {
    expectExact(browsersUri({ q: ' heat shock, mouse ' }), '/api/graph/v1/browsers?q=heat+shock,+mouse')
  })

  it('a blank browsers q is not sent', () => {
    expectExact(browsersUri({ q: '   ' }), '/api/graph/v1/browsers')
  })
})

describe('equal keys', () => {
  it.each(['seeds', ...networkLists])('network: %s is ordered and deduplicated', (name) => {
    expect(networkHash({ ...seeded, [name]: ['b', 'a', 'b'] })).toBe(networkHash({ ...seeded, [name]: ['a', 'b'] }))
  })

  it.each(filterLists)('relationship and evidence: %s is ordered and deduplicated', (name) => {
    expect(relationshipHash({ [name]: ['b', 'a', 'b'] })).toBe(relationshipHash({ [name]: ['a', 'b'] }))
    expect(evidenceHash({ [name]: ['b', 'a', 'b'] })).toBe(evidenceHash({ [name]: ['a', 'b'] }))
  })

  it('resolve and entity: releases is ordered and deduplicated', () => {
    expect(resolveHash({ q: ['a'], releases: [v2, v1, v1] })).toBe(resolveHash({ q: ['a'], releases: [v1, v2] }))
    expect(entityHash({ releases: [v2, v1, v1] })).toBe(entityHash({ releases: [v1, v2] }))
  })

  it.each(networkOptional)('network: %s as null or undefined reads as absent', (name) => {
    expect(networkHash({ ...seeded, [name]: null })).toBe(networkHash(seeded))
    expect(networkHash({ ...seeded, [name]: undefined })).toBe(networkHash(seeded))
  })

  it.each(resolveOptional)('resolve: %s as null or undefined reads as absent', (name) => {
    expect(resolveHash({ q: ['a'], [name]: null })).toBe(resolveHash({ q: ['a'] }))
    expect(resolveHash({ q: ['a'], [name]: undefined })).toBe(resolveHash({ q: ['a'] }))
  })

  it.each(selectionNames)('entity: %s as null or undefined reads as absent', (name) => {
    expect(entityHash({ [name]: null })).toBe(entityHash())
    expect(entityHash({ [name]: undefined })).toBe(entityHash())
  })

  it.each(filterNames)('relationship and evidence: %s as null or undefined reads as absent', (name) => {
    expect(relationshipHash({ [name]: null })).toBe(relationshipHash())
    expect(relationshipHash({ [name]: undefined })).toBe(relationshipHash())
    expect(evidenceHash({ [name]: null })).toBe(evidenceHash())
    expect(evidenceHash({ [name]: undefined })).toBe(evidenceHash())
  })

  it('evidence: page_size as null or undefined reads as absent', () => {
    expect(evidenceHash({ page_size: null })).toBe(evidenceHash())
    expect(evidenceHash({ page_size: undefined })).toBe(evidenceHash())
  })

  it.each(browsersOptional)('browsers: %s as null or undefined reads as absent', (name) => {
    expect(browsersHash({ [name]: null })).toBe(browsersHash())
    expect(browsersHash({ [name]: undefined })).toBe(browsersHash())
  })

  it('a null selection or filters argument reads as none', () => {
    expect(entityHash(null)).toBe(entityHash())
    expect(relationshipHash(null)).toBe(relationshipHash())
    expect(evidenceHash(null)).toBe(evidenceHash())
    expect(browsersHash(null)).toBe(browsersHash())
  })

  it('a null context value reads as absent', () => {
    expect(networkHash({ ...seeded, context: { tissue: null } })).toBe(networkHash(seeded))
  })

  it.each([null, undefined])('a list element of %s reads as absent', (element) => {
    expect(networkHash({ seeds: [tf1, element] })).toBe(networkHash(seeded))
    expect(networkHash({ ...seeded, layers: ['a', element] })).toBe(networkHash({ ...seeded, layers: ['a'] }))
    expect(resolveHash({ q: ['a', element] })).toBe(resolveHash({ q: ['a'] }))
  })

  it('context key order does not change the key', () => {
    expect(networkHash({ ...seeded, context: { tissue: 'a', condition: 'b' } })).toBe(
      networkHash({ ...seeded, context: { condition: 'b', tissue: 'a' } }),
    )
  })

  it('an absent list and one set to undefined are one key', () => {
    expect(networkHash({ ...seeded, layers: undefined })).toBe(networkHash(seeded))
  })
})

describe('distinct keys', () => {
  it.each<[string, unknown]>([
    ...filterValues,
    ['seeds', [tf2]],
    ['layers', ['x']],
    ['direction', 'out'],
    ['taxon', 1],
    ['include_partner_edges', true],
    ['max_edges', 2],
  ])('network: %s', (name, value) => {
    expect(networkHash({ ...seeded, [name]: value })).not.toBe(networkHash(seeded))
  })

  it.each<[string, unknown]>([
    ['q', ['b']],
    ['namespace', 'n'],
    ['taxon', 1],
    ['entity_type', 'e'],
    ['releases', [v1]],
    ['browser', 's'],
  ])('resolve: %s', (name, value) => {
    expect(resolveHash({ q: ['a'], [name]: value })).not.toBe(resolveHash({ q: ['a'] }))
  })

  it.each<[string, unknown]>([
    ['releases', [v1]],
    ['browser', 's'],
  ])('entity: %s', (name, value) => {
    expect(entityHash({ [name]: value })).not.toBe(entityHash())
  })

  it.each(filterValues)('relationship: %s', (name, value) => {
    expect(relationshipHash({ [name]: value })).not.toBe(relationshipHash())
  })

  it.each<[string, unknown]>([...filterValues, ['page_size', 2]])('evidence: %s', (name, value) => {
    expect(evidenceHash({ [name]: value })).not.toBe(evidenceHash())
  })

  it.each<[string, unknown]>([
    ['q', 'x'],
    ['tag', 't'],
    ['relationship_type', 'r'],
    ['taxon', 5],
    ['assay', 'a'],
    ['year', 2020],
    ['status', 'available'],
    ['availability', 'live'],
  ])('browsers: %s', (name, value) => {
    expect(browsersHash({ [name]: value })).not.toBe(browsersHash())
  })

  it('the id, the ref or the slug in a path', () => {
    expect(hashKey(graphEntityOptions('staff', tf1).queryKey)).not.toBe(hashKey(graphEntityOptions('staff', tf2).queryKey))
    expect(hashKey(graphRelationshipOptions('staff', mixed).queryKey)).not.toBe(
      hashKey(graphRelationshipOptions('staff', demoRefs.forward).queryKey),
    )
    expect(hashKey(graphEvidenceOptions('staff', mixed).queryKey)).not.toBe(
      hashKey(graphEvidenceOptions('staff', demoRefs.forward).queryKey),
    )
    expect(hashKey(graphBrowserOptions('staff', 'a').queryKey)).not.toBe(hashKey(graphBrowserOptions('staff', 'b').queryKey))
  })

  const viewers: GraphViewer[] = ['staff', 'account', 'visitor']
  it.each<[string, (viewer: GraphViewer) => QueryKey]>([
    ['resolve', (viewer) => graphResolveOptions(viewer, { q: ['a'] }).queryKey],
    ['entity', (viewer) => graphEntityOptions(viewer, tf1).queryKey],
    ['network', (viewer) => graphNetworkOptions(viewer, seeded).queryKey],
    ['relationship', (viewer) => graphRelationshipOptions(viewer, mixed).queryKey],
    ['evidence', (viewer) => graphEvidenceOptions(viewer, mixed).queryKey],
    ['browsers', (viewer) => graphBrowsersOptions(viewer).queryKey],
    ['browser', (viewer) => graphBrowserOptions(viewer, 's').queryKey],
  ])('each viewer has its own key on %s', (_name, keyOf) => {
    expect(new Set(viewers.map((viewer) => hashKey(keyOf(viewer)))).size).toBe(3)
  })

  it('defaults are not collapsed', () => {
    expect(networkHash({ ...seeded, direction: 'both' })).not.toBe(networkHash(seeded))
    expect(networkHash({ ...seeded, outcome: ['supported'] })).not.toBe(networkHash(seeded))
    expect(evidenceHash({ outcome: ['supported'] })).not.toBe(evidenceHash())
  })

  it('page_size is in an evidence key and after is not', () => {
    const key = graphEvidenceOptions('staff', mixed, { page_size: 2 }).queryKey
    expect(key[key.length - 1]).toHaveProperty('page_size', 2)
    expect(JSON.stringify(key)).not.toContain('after')
  })

  it('a relationship page and its evidence never share a key', () => {
    expect(relationshipHash()).not.toBe(evidenceHash())
  })
})

describe('enabled', () => {
  it.each(['seeds', ...networkLists])('network: %s [] disables', (name) => {
    expect(graphNetworkOptions('staff', { ...seeded, [name]: [] } as GraphNetworkParams).enabled).toBe(false)
  })

  it('network: seeds empty after cleaning, or null, disables', () => {
    expect(graphNetworkOptions('staff', { seeds: [''] }).enabled).toBe(false)
    expect(graphNetworkOptions('staff', { seeds: null } as never).enabled).toBe(false)
  })

  it.each([null, undefined])('a list holding only %s disables', (element) => {
    expect(graphNetworkOptions('staff', { seeds: [element] }).enabled).toBe(false)
    expect(graphNetworkOptions('staff', { ...seeded, layers: [element] }).enabled).toBe(false)
    expect(graphResolveOptions('staff', { q: [element] }).enabled).toBe(false)
    expect(graphRelationshipOptions('staff', mixed, { method: [element] }).enabled).toBe(false)
  })

  it.each(filterLists)('relationship and evidence: %s [] disables', (name) => {
    expect(graphRelationshipOptions('staff', mixed, { [name]: [] }).enabled).toBe(false)
    expect(graphEvidenceOptions('staff', mixed, { [name]: [] }).enabled).toBe(false)
  })

  it('resolve: no term left, or releases [], disables', () => {
    expect(graphResolveOptions('staff', { q: [] }).enabled).toBe(false)
    expect(graphResolveOptions('staff', { q: ['  '] }).enabled).toBe(false)
    expect(graphResolveOptions('staff', { q: null } as never).enabled).toBe(false)
    expect(graphResolveOptions('staff', { q: ['a'], releases: [] }).enabled).toBe(false)
  })

  it('an empty id, ref or slug disables', () => {
    expect(graphEntityOptions('staff', '').enabled).toBe(false)
    expect(graphRelationshipOptions('staff', '').enabled).toBe(false)
    expect(graphEvidenceOptions('staff', '').enabled).toBe(false)
    expect(graphBrowserOptions('staff', '').enabled).toBe(false)
  })

  it.each([null, undefined])('an id, ref or slug of %s disables', (absent) => {
    expect(graphEntityOptions('staff', absent).enabled).toBe(false)
    expect(graphRelationshipOptions('staff', absent).enabled).toBe(false)
    expect(graphEvidenceOptions('staff', absent).enabled).toBe(false)
    expect(graphBrowserOptions('staff', absent).enabled).toBe(false)
  })

  it.each(['.', '..'])('an id, ref or slug of %s disables: a URL parser would remove it', (dots) => {
    expect(graphEntityOptions('staff', dots).enabled).toBe(false)
    expect(graphRelationshipOptions('staff', dots).enabled).toBe(false)
    expect(graphEvidenceOptions('staff', dots).enabled).toBe(false)
    expect(graphBrowserOptions('staff', dots).enabled).toBe(false)
  })

  it.each(['...', 'a..b', '.a', 'a.'])('an id, ref or slug of %s is one segment, and enabled', (dotted) => {
    expect(graphEntityOptions('staff', dotted).enabled).toBe(true)
    expect(graphRelationshipOptions('staff', dotted).enabled).toBe(true)
    expect(graphEvidenceOptions('staff', dotted).enabled).toBe(true)
    expect(graphBrowserOptions('staff', dotted).enabled).toBe(true)
  })

  it('entity: releases [] disables', () => {
    expect(graphEntityOptions('staff', tf1, { releases: [] }).enabled).toBe(false)
  })

  it('a usable request is enabled on every factory that decides', () => {
    expect(graphResolveOptions('staff', { q: ['a'] }).enabled).toBe(true)
    expect(graphEntityOptions('staff', tf1).enabled).toBe(true)
    expect(graphNetworkOptions('staff', seeded).enabled).toBe(true)
    expect(graphRelationshipOptions('staff', mixed).enabled).toBe(true)
    expect(graphEvidenceOptions('staff', mixed).enabled).toBe(true)
    expect(graphBrowserOptions('staff', 's').enabled).toBe(true)
  })

  it('the gallery is enabled, so a caller can disable it', () => {
    expect(graphBrowsersOptions('staff').enabled).toBe(true)
  })

  it('status and vocabularies are never disabled', () => {
    expect(graphStatusOptions().enabled).not.toBe(false)
    expect(graphVocabulariesOptions().enabled).not.toBe(false)
  })
})

describe('what is never sent', () => {
  it('a blank direction', () => {
    const params = { ...seeded, direction: '' as never }
    expectExact(networkUri(params), NETWORK_URI)
    expect(networkHash(params)).toBe(networkHash(seeded))
  })

  it('a blank context value', () => {
    const params = { ...seeded, context: { tissue: '' } }
    expectExact(networkUri(params), NETWORK_URI)
    expect(networkHash(params)).toBe(networkHash(seeded))
  })

  it.each([null, undefined])('a list element of %s', (element) => {
    expectExact(networkUri({ seeds: [tf1, element] }), NETWORK_URI)
    expectExact(resolveUri({ q: ['a', element] }), '/api/graph/v1/resolve?q=a')
  })

  // parseInt('') on a cleared number box.
  it.each<[string, () => string, string, () => string, () => string]>([
    [
      'network taxon',
      () => networkUri({ ...seeded, taxon: NaN }),
      NETWORK_URI,
      () => networkHash({ ...seeded, taxon: NaN }),
      () => networkHash(seeded),
    ],
    [
      'network max_edges',
      () => networkUri({ ...seeded, max_edges: NaN }),
      NETWORK_URI,
      () => networkHash({ ...seeded, max_edges: NaN }),
      () => networkHash(seeded),
    ],
    [
      'resolve taxon',
      () => resolveUri({ q: ['a'], taxon: NaN }),
      '/api/graph/v1/resolve?q=a',
      () => resolveHash({ q: ['a'], taxon: NaN }),
      () => resolveHash({ q: ['a'] }),
    ],
    [
      'evidence page_size',
      () => uriOf(EVIDENCE_PATH, evidenceWire(normalizeEvidenceParams({ page_size: NaN }), null)),
      `/api${EVIDENCE_PATH}`,
      () => evidenceHash({ page_size: NaN }),
      () => evidenceHash(),
    ],
    [
      'browsers taxon',
      () => browsersUri({ taxon: NaN }),
      '/api/graph/v1/browsers',
      () => browsersHash({ taxon: NaN }),
      () => browsersHash(),
    ],
    [
      'browsers year',
      () => browsersUri({ year: NaN }),
      '/api/graph/v1/browsers',
      () => browsersHash({ year: NaN }),
      () => browsersHash(),
    ],
  ])('NaN as the %s', (_name, uri, expected, hash, absent) => {
    expectExact(uri(), expected)
    expect(hash()).toBe(absent())
  })

  it.each(['status', 'availability'])('a blank browsers %s', (name) => {
    const filters = { [name]: '' } as GraphBrowsersParams
    expectExact(browsersUri(filters), '/api/graph/v1/browsers')
    expect(browsersHash(filters)).toBe(browsersHash())
  })

  it.each<[string, GraphNetworkParams]>([
    ['a comma-joined list', { seeds: [] }],
    ['the repeated list', { seeds: [tf1], score: [] }],
  ])('a wire builder refuses [] in %s', (_name, params) => {
    expect(() => networkWire(params)).toThrow(/empty list/)
  })

  it('browser with releases: only browser is kept', () => {
    expect(normalizeSelection({ browser: 's', releases: [v1] } as never)).toEqual({ browser: 's' })
    // @ts-expect-error browser and releases exclude each other
    const bad: GraphSelection = { browser: 's', releases: [v1] }
    expect(normalizeSelection(bad)).toEqual({ browser: 's' })
  })
})

interface SharedOptions {
  queryKey: QueryKey
  retry?: unknown
  retryOnMount?: unknown
  refetchOnMount?: unknown
  refetchOnReconnect?: unknown
  refetchOnWindowFocus?: unknown
  staleTime?: unknown
}

describe('every factory', () => {
  const minute = 60 * 1000
  const hour = 60 * minute

  it.each<[string, () => SharedOptions, string, number]>([
    ['status', () => graphStatusOptions(), 'status', 5 * minute],
    ['vocabularies', () => graphVocabulariesOptions(), 'vocabularies', hour],
    ['resolve', () => graphResolveOptions('staff', { q: ['a'] }), 'resolve', minute],
    ['entity', () => graphEntityOptions('staff', tf1), 'entity', minute],
    ['network', () => graphNetworkOptions('staff', seeded), 'network', minute],
    ['relationship', () => graphRelationshipOptions('staff', mixed), 'relationship', minute],
    ['evidence', () => graphEvidenceOptions('staff', mixed), 'relationship', minute],
    ['browsers', () => graphBrowsersOptions('staff'), 'browsers', minute],
    ['browser', () => graphBrowserOptions('staff', 's'), 'browsers', minute],
  ])('%s carries the shared options', (_name, make, group, staleTime) => {
    const options = make()
    expect(options.queryKey.slice(0, 2)).toEqual(['graph', group])
    expect(options.retry).toBe(retryGraphQuery)
    expect(options.retryOnMount).toBe(graphMayResend)
    expect(options.refetchOnMount).toBe(graphMayResend)
    expect(options.refetchOnReconnect).toBe(graphMayResend)
    expect(options.refetchOnWindowFocus).toBe(false)
    expect(options.staleTime).toBe(staleTime)
  })

  it('vocabularies outlive an hour with nothing mounted', () => {
    expect(graphVocabulariesOptions().gcTime).toBe(hour)
  })
})

describe('the context facets', () => {
  it("are the API's twelve, in the API's order", () => {
    expect([...GRAPH_CONTEXT_FACETS]).toEqual(
      recorded<GraphVocabularies>('vocabularies').body.context_facets.map((entry) => entry.value),
    )
  })

  it("are each on the network route's list", () => {
    const route = typedGraphRoutes.find((candidate) => candidate.path === '/api/graph/v1/network')
    for (const facet of GRAPH_CONTEXT_FACETS) {
      expect(route.allowed).toContain(`context.${facet}`)
    }
  })
})
