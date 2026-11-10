import { http, HttpResponse } from 'msw'
import type { HttpHandler, JsonBodyType } from 'msw'
import type { RecordedCaller, RecordedEntry } from '../fixtures/typedGraph/recorded'

export interface TypedGraphRoute {
  path: string
  allowed: string[]
  /** A download: no hook and no recording, so coverage.test.ts skips it (W34). */
  stream?: boolean
}

export const typedGraphRoutes: readonly TypedGraphRoute[] =
// <<< typed-graph-routes
[
  {"path": "/api/graph/v1/status", "allowed": []},
  {"path": "/api/graph/v1/vocabularies", "allowed": []},
  {"path": "/api/graph/v1/resolve", "allowed": ["q", "namespace", "taxon", "entity_type", "releases", "browser"]},
  {"path": "/api/graph/v1/entities/{entity_id}", "allowed": ["releases", "browser"]},
  {"path": "/api/graph/v1/network", "allowed": ["seeds", "layers", "direction", "outcome", "evidence_basis", "effect", "method", "level", "score", "context.host_taxon", "context.tissue", "context.cell_type", "context.cell_line", "context.condition", "context.perturbation", "context.compartment", "context.assay_system", "context.developmental_stage", "context.timepoint", "context.genome_assembly", "context.other", "taxon", "include_partner_edges", "max_edges", "releases", "browser"]},
  {"path": "/api/graph/v1/relationships/{relationship_ref}", "allowed": ["outcome", "evidence_basis", "effect", "method", "level", "score", "context.host_taxon", "context.tissue", "context.cell_type", "context.cell_line", "context.condition", "context.perturbation", "context.compartment", "context.assay_system", "context.developmental_stage", "context.timepoint", "context.genome_assembly", "context.other", "releases", "browser"]},
  {"path": "/api/graph/v1/relationships/{relationship_ref}/evidence", "allowed": ["outcome", "evidence_basis", "effect", "method", "level", "score", "context.host_taxon", "context.tissue", "context.cell_type", "context.cell_line", "context.condition", "context.perturbation", "context.compartment", "context.assay_system", "context.developmental_stage", "context.timepoint", "context.genome_assembly", "context.other", "after", "page_size", "releases", "browser"]},
  {"path": "/api/graph/v1/browsers", "allowed": ["q", "tag", "relationship_type", "taxon", "assay", "year", "status", "availability"]},
  {"path": "/api/graph/v1/browsers/{slug}", "allowed": []}
]
// >>> typed-graph-routes

const BASE = '/api/graph/v1'
const RESOLVE = `${BASE}/resolve`
/** These do not vary by caller (views.py: status reads no token, vocabularies no caller). */
const CALLER_FREE = new Set([`${BASE}/status`, `${BASE}/vocabularies`])
const COMMA_LISTS = new Set(['seeds', 'layers', 'outcome', 'evidence_basis', 'effect', 'method', 'level', 'releases'])

type Pairs = readonly (readonly [string, string])[]

function uniqueSorted(values: string[]): string[] {
  return Array.from(new Set(values)).sort()
}

function canonicalKey(path: string, pairs: Pairs): string {
  const byName = new Map<string, string[]>()
  for (const [name, value] of pairs) {
    byName.set(name, [...(byName.get(name) ?? []), value])
  }
  const parts = Array.from(byName.keys())
    .sort()
    .flatMap((name) => {
      const values = byName.get(name) as string[]
      if (name === 'q' && path === RESOLVE) return [`q=${values.join(',')}`]
      if (COMMA_LISTS.has(name)) {
        return [`${name}=${uniqueSorted(values.flatMap((value) => value.split(','))).join(',')}`]
      }
      return uniqueSorted(values).map((value) => `${name}=${value}`)
    })
  return parts.length > 0 ? `${path}?${parts.join('&')}` : path
}

interface Tables {
  entries: Record<RecordedCaller, Map<string, RecordedEntry>>
  offNotFound: RecordedEntry
}

function buildTables(recorded: readonly RecordedEntry[]): Tables {
  const entries: Tables['entries'] = { staff: new Map(), visitor: new Map(), off: new Map() }
  for (const entry of recorded) {
    entries[entry.caller].set(canonicalKey(entry.path, entry.query), entry)
  }
  const offNotFound = recorded.find((entry) => entry.name === 'off-not-found') as RecordedEntry
  return { entries, offNotFound }
}

let tables: Promise<Tables> | undefined

/** The fixtures load on the first typed request, so no other test pays for them. */
function loadTables(): Promise<Tables> {
  if (tables === undefined) {
    tables = import('../fixtures/typedGraph/index').then(({ recordedEntries }) => buildTables(recordedEntries))
  }
  return tables
}

function respond(entry: RecordedEntry) {
  return HttpResponse.json(entry.body as JsonBodyType, { status: entry.status })
}

function miss(key: string) {
  return HttpResponse.json({ detail: `No recorded typed-graph response for ${key}.`, key }, { status: 501 })
}

/** The API's body for names a route does not take (params.py). A name holding a quote is not mirrored exactly, nor is the cap of 20 problems, nor the second problem when a required parameter is also missing. */
function unknownParameters(names: string[], allowed: string[]) {
  const listed = allowed.length > 0 ? `Allowed: ${allowed.join(', ')}.` : 'This route takes no parameters.'
  return HttpResponse.json(
    {
      detail: names.map((name) => `Unknown parameter '${name}'. ${listed}`).join(' '),
      errors: names.map((name) => ({ parameter: name, value: null, allowed: [...allowed], limit: null })),
    },
    { status: 400 },
  )
}

function answer(route: TypedGraphRoute, caller: RecordedCaller) {
  return async ({ request }: { request: Request }) => {
    const url = new URL(request.url)
    const pairs: Pairs = Array.from(url.searchParams.entries())
    const path = decodeURIComponent(url.pathname)
    const { entries } = await loadTables()
    const key = canonicalKey(path, pairs)
    const entry = entries[caller].get(key) ?? (CALLER_FREE.has(path) ? entries.staff.get(key) : undefined)
    if (entry) return respond(entry)
    const names = Array.from(new Set(pairs.map(([name]) => name)))
    const unknown = names.filter((name) => name !== 'format' && !route.allowed.includes(name))
    return unknown.length > 0 ? unknownParameters(unknown, route.allowed) : miss(key)
  }
}

function routeHandlers(caller: RecordedCaller): HttpHandler[] {
  return typedGraphRoutes.map((route) =>
    http.get(route.path.replace(/\{(\w+)\}/g, ':$1'), answer(route, caller)),
  )
}

/** Staff's recordings, so any test sees the demonstration. A trailing catch-all answers 501, so nothing typed goes out for real. */
export const typedGraphHandlers: HttpHandler[] = [
  ...routeHandlers('staff'),
  http.all(`${BASE}/*`, ({ request }) => miss(`${request.method} ${new URL(request.url).pathname}`)),
]

/** For `server.use`: a caller with no account, who on the demonstration sees nothing. */
export const typedGraphVisitorHandlers: HttpHandler[] = routeHandlers('visitor')

/** For `server.use`: TYPED_GRAPH_ENABLED off. `status` answers, every other route is the JSON 404 before anything runs. */
export const typedGraphOffHandlers: HttpHandler[] = [
  http.get(`${BASE}/status`, answer(typedGraphRoutes[0], 'off')),
  http.all(`${BASE}/*`, async () => respond((await loadTables()).offNotFound)),
]
