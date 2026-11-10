import { isAxiosError, isCancel } from 'axios'
import type { GraphProblem } from '../../features/graph/types'

/** Thrown by the GET helper instead of sending a line no server would accept. */
export class GraphRequestTooLongError extends Error {
  readonly bytes: number
  readonly limit: number

  constructor(bytes: number, limit: number) {
    super(`This request line would be ${bytes} bytes; a server accepts ${limit}.`)
    this.name = 'GraphRequestTooLongError'
    this.bytes = bytes
    this.limit = limit
  }
}

/** A 2xx with no JSON object: an HTML page from a proxy or the SPA's fallback, or an empty body. */
export class GraphBadBodyError extends Error {
  readonly status: number

  constructor(status: number) {
    super(`The API answered ${status} with no JSON object.`)
    this.name = 'GraphBadBodyError'
    this.status = status
  }
}

export type GraphErrorInfo =
  | { kind: 'cancelled' }
  /** The GET helper refused to send it. */
  | { kind: 'too_long'; bytes: number; limit: number }
  /** axios's 30 s timeout. */
  | { kind: 'timeout' }
  /** No response: a network failure, or a closed connection (the HTTP/2 case of a long line). */
  | { kind: 'network' }
  /** The API's own 400. `errors` may be `[]`. */
  | { kind: 'bad_request'; status: 400; detail: string; errors: GraphProblem[] }
  /** A web server's HTML 400 or 414: a request line too long, or another front-end refusal. */
  | { kind: 'server_refused'; status: 400 | 414 }
  | { kind: 'unauthorized'; status: 401 }
  /** The API's own 404: not found, invisible, or the switch off. They read alike. */
  | { kind: 'not_found'; status: 404; detail: string }
  | { kind: 'not_acceptable'; status: 406 }
  /** `retryAfter` in seconds, or null: the header can be absent, and unreadable cross-origin. */
  | { kind: 'throttled'; status: 429; detail: string | null; retryAfter: number | null }
  /** The API's own 503: narrow the request; never repeat it unchanged. */
  | { kind: 'too_expensive'; status: 503; detail: string }
  /** A 2xx that is not a JSON object. */
  | { kind: 'bad_body'; status: number }
  /** Anything else: a non-JSON 404, a Django 500, a proxy's 502 or 503, a 501 from the MSW mirror. */
  | { kind: 'http'; status: number; json: boolean }
  /** Not from axios. */
  | { kind: 'unknown' }

function objectBody(data: unknown): Record<string, unknown> | null {
  return typeof data === 'object' && data !== null && !Array.isArray(data)
    ? (data as Record<string, unknown>)
    : null
}

function retryAfterSeconds(header: unknown): number | null {
  return typeof header === 'string' && /^\d+$/.test(header) ? Number(header) : null
}

export function classifyGraphError(error: unknown): GraphErrorInfo {
  if (error instanceof GraphBadBodyError) return { kind: 'bad_body', status: error.status }
  if (error instanceof GraphRequestTooLongError) {
    return { kind: 'too_long', bytes: error.bytes, limit: error.limit }
  }
  if (isCancel(error)) return { kind: 'cancelled' }
  if (!isAxiosError(error)) return { kind: 'unknown' }
  if (error.code === 'ERR_CANCELED') return { kind: 'cancelled' }

  const response = error.response
  if (!response) return error.code === 'ECONNABORTED' ? { kind: 'timeout' } : { kind: 'network' }

  const { status } = response
  const body = objectBody(response.data)
  const detail = typeof body?.detail === 'string' ? body.detail : null

  if (status === 400 && body && Array.isArray(body.errors)) {
    return { kind: 'bad_request', status, detail: detail ?? '', errors: body.errors as GraphProblem[] }
  }
  if ((status === 400 || status === 414) && !body) return { kind: 'server_refused', status }
  if (status === 429) {
    const retryAfter: unknown = response.headers['retry-after']
    return { kind: 'throttled', status, detail, retryAfter: retryAfterSeconds(retryAfter) }
  }
  if (status === 503 && detail !== null) return { kind: 'too_expensive', status, detail }
  if (status === 404 && detail !== null) return { kind: 'not_found', status, detail }
  if (status === 401) return { kind: 'unauthorized', status }
  if (status === 406) return { kind: 'not_acceptable', status }
  return { kind: 'http', status, json: body !== null }
}

/** One predicate for every typed query (W5). */
export function retryGraphQuery(failureCount: number, error: Error): boolean {
  if (failureCount >= 1) return false
  const info = classifyGraphError(error)
  switch (info.kind) {
    case 'unauthorized': // the interceptor logged out; a fetchQuery's retry goes out anonymously (a hook has moved to the visitor key)
    case 'network':
      return true
    case 'http': // a restarting worker or a gateway; any other 5xx would only repeat
      return !info.json && (info.status === 502 || info.status === 503)
    default:
      return false
  }
}

/** False for a stored failure a repeat cannot change, or that the API says not to repeat. */
export function graphMayResend(query: { state: { error: unknown } }): boolean {
  switch (classifyGraphError(query.state.error).kind) {
    case 'too_expensive':
    case 'bad_request':
    case 'server_refused':
    case 'too_long':
    case 'not_acceptable':
      return false
    default:
      return true
  }
}
