import { AxiosError, CanceledError } from 'axios'
import { describe, expect, it } from 'vitest'
import {
  classifyGraphError,
  GraphBadBodyError,
  graphMayResend,
  GraphRequestTooLongError,
  retryGraphQuery,
} from './errors'
import type { GraphErrorInfo } from './errors'

function answered(status: number, data: unknown, headers: Record<string, string> = {}): AxiosError {
  return new AxiosError('x', undefined, undefined, undefined, {
    status,
    statusText: '',
    headers,
    config: {} as never,
    data,
  })
}

interface Row {
  name: string
  input: unknown
  expected: GraphErrorInfo
  retried: boolean
  resent: boolean
}

const problem = { parameter: 'q', value: null, allowed: [], limit: null }

const rows: Row[] = [
  {
    name: '400, an errors array',
    input: answered(400, { detail: 'd', errors: [problem] }),
    expected: { kind: 'bad_request', status: 400, detail: 'd', errors: [problem] },
    retried: false,
    resent: false,
  },
  {
    name: '400, an empty errors array',
    input: answered(400, { detail: 'Too many query parameters.', errors: [] }),
    expected: { kind: 'bad_request', status: 400, detail: 'Too many query parameters.', errors: [] },
    retried: false,
    resent: false,
  },
  {
    name: '400, an HTML page',
    input: answered(400, '<html>'),
    expected: { kind: 'server_refused', status: 400 },
    retried: false,
    resent: false,
  },
  {
    name: '414, an HTML page',
    input: answered(414, '<html>'),
    expected: { kind: 'server_refused', status: 414 },
    retried: false,
    resent: false,
  },
  {
    name: '400, a JSON array',
    input: answered(400, ['x']),
    expected: { kind: 'server_refused', status: 400 },
    retried: false,
    resent: false,
  },
  {
    name: '401',
    input: answered(401, {}),
    expected: { kind: 'unauthorized', status: 401 },
    retried: true,
    resent: true,
  },
  {
    name: '404, JSON',
    input: answered(404, { detail: 'Not found.' }),
    expected: { kind: 'not_found', status: 404, detail: 'Not found.' },
    retried: false,
    resent: true,
  },
  {
    name: '404, an HTML page',
    input: answered(404, '<html>'),
    expected: { kind: 'http', status: 404, json: false },
    retried: false,
    resent: true,
  },
  {
    name: '406',
    input: answered(406, { detail: 'd' }),
    expected: { kind: 'not_acceptable', status: 406 },
    retried: false,
    resent: false,
  },
  {
    name: '429, a Retry-After in seconds',
    input: answered(429, { detail: 'd' }, { 'retry-after': '7' }),
    expected: { kind: 'throttled', status: 429, detail: 'd', retryAfter: 7 },
    retried: false,
    resent: true,
  },
  {
    name: '429, no header',
    input: answered(429, { detail: 'd' }),
    expected: { kind: 'throttled', status: 429, detail: 'd', retryAfter: null },
    retried: false,
    resent: true,
  },
  {
    name: '429, a Retry-After that is a date',
    input: answered(429, { detail: 'd' }, { 'retry-after': 'Wed, 21 Oct 2026 07:28:00 GMT' }),
    expected: { kind: 'throttled', status: 429, detail: 'd', retryAfter: null },
    retried: false,
    resent: true,
  },
  {
    name: '429, an HTML page',
    input: answered(429, '<html>'),
    expected: { kind: 'throttled', status: 429, detail: null, retryAfter: null },
    retried: false,
    resent: true,
  },
  {
    name: '400, JSON with no errors',
    input: answered(400, { detail: 'd' }),
    expected: { kind: 'http', status: 400, json: true },
    retried: false,
    resent: true,
  },
  {
    name: '503, JSON',
    input: answered(503, { detail: 'd' }),
    expected: { kind: 'too_expensive', status: 503, detail: 'd' },
    retried: false,
    resent: false,
  },
  {
    name: '503, an HTML page',
    input: answered(503, '<html>'),
    expected: { kind: 'http', status: 503, json: false },
    retried: true,
    resent: true,
  },
  {
    name: '502, an empty body',
    input: answered(502, ''),
    expected: { kind: 'http', status: 502, json: false },
    retried: true,
    resent: true,
  },
  {
    name: '500, an HTML page',
    input: answered(500, '<html>'),
    expected: { kind: 'http', status: 500, json: false },
    retried: false,
    resent: true,
  },
  {
    name: '504, an HTML page',
    input: answered(504, '<html>'),
    expected: { kind: 'http', status: 504, json: false },
    retried: false,
    resent: true,
  },
  {
    name: "501, the mirror's JSON",
    input: answered(501, { detail: 'd', key: 'k' }),
    expected: { kind: 'http', status: 501, json: true },
    retried: false,
    resent: true,
  },
  {
    name: '502, JSON',
    input: answered(502, { detail: 'd' }),
    expected: { kind: 'http', status: 502, json: true },
    retried: false,
    resent: true,
  },
  {
    name: '503, JSON with no string detail',
    input: answered(503, {}),
    expected: { kind: 'http', status: 503, json: true },
    retried: false,
    resent: true,
  },
  {
    name: '500, a JSON null',
    input: answered(500, null),
    expected: { kind: 'http', status: 500, json: false },
    retried: false,
    resent: true,
  },
  {
    name: 'no response, ECONNABORTED',
    input: new AxiosError('x', 'ECONNABORTED'),
    expected: { kind: 'timeout' },
    retried: false,
    resent: true,
  },
  {
    name: 'no response, ERR_NETWORK',
    input: new AxiosError('x', 'ERR_NETWORK'),
    expected: { kind: 'network' },
    retried: true,
    resent: true,
  },
  {
    name: 'a cancel',
    input: new CanceledError(),
    expected: { kind: 'cancelled' },
    retried: false,
    resent: true,
  },
  {
    name: 'an axios error coded ERR_CANCELED',
    input: new AxiosError('x', 'ERR_CANCELED'),
    expected: { kind: 'cancelled' },
    retried: false,
    resent: true,
  },
  {
    name: 'a line too long',
    input: new GraphRequestTooLongError(5000, 4094),
    expected: { kind: 'too_long', bytes: 5000, limit: 4094 },
    retried: false,
    resent: false,
  },
  {
    name: 'a 2xx with no JSON object',
    input: new GraphBadBodyError(200),
    expected: { kind: 'bad_body', status: 200 },
    retried: false,
    resent: true,
  },
  {
    name: 'a plain error',
    input: new Error('x'),
    expected: { kind: 'unknown' },
    retried: false,
    resent: true,
  },
  {
    name: 'a thrown string',
    input: 'text',
    expected: { kind: 'unknown' },
    retried: false,
    resent: true,
  },
]

describe('classifyGraphError', () => {
  it.each(rows)('$name', ({ input, expected }) => {
    expect(classifyGraphError(input)).toEqual(expected)
  })
})

describe('retryGraphQuery', () => {
  it.each(rows)('$name, first failure', ({ input, retried }) => {
    expect(retryGraphQuery(0, input as Error)).toBe(retried)
  })

  it.each(rows)('$name, second failure', ({ input }) => {
    expect(retryGraphQuery(1, input as Error)).toBe(false)
  })
})

describe('graphMayResend', () => {
  it.each(rows)('$name', ({ input, resent }) => {
    expect(graphMayResend({ state: { error: input } })).toBe(resent)
  })

  it('a query with no error may be sent', () => {
    expect(graphMayResend({ state: { error: null } })).toBe(true)
  })
})
