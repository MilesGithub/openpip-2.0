import { classifyGraphError, graphMayResend } from '../../../api/graph/errors'
import type { GraphFeatureState } from '../../../api/graph/status'
import { PAGE_LOADING, PAGE_UNKNOWN, SERVER_UNREACHABLE, throttledText } from './pageWords'

export interface GateView { text: string; retry: boolean }
/** null for `on`, when the gate renders its children. */
export function gateView(state: GraphFeatureState, notEnabled: string): GateView | null {
  switch (state) {
    case 'loading':
      return { text: PAGE_LOADING, retry: false }
    case 'off':
      return { text: notEnabled, retry: false }
    case 'on':
      return null
    case 'unknown':
    default:
      return { text: PAGE_UNKNOWN, retry: true }
  }
}

export interface RouteErrorWords { other: string; notFound: string | null; timeout: string }
export interface RouteErrorView { text: string; retry: boolean; recheck: boolean }
export function routeErrorView(error: unknown, words: RouteErrorWords): RouteErrorView {
  const info = classifyGraphError(error)
  switch (info.kind) {
    case 'not_found':
      return { text: words.notFound ?? words.other, retry: words.notFound === null, recheck: true }
    case 'throttled':
      return { text: throttledText(info.retryAfter), retry: true, recheck: false }
    case 'network':
      return { text: SERVER_UNREACHABLE, retry: true, recheck: false }
    case 'timeout':
      // axios gave up after 30 s, so the server may have been reached and may still be answering.
      return { text: words.timeout, retry: true, recheck: false }
    default:
      // No "Try again" where a repeat cannot change the answer: F1's graphMayResend names those kinds, and
      // after a 401 the hook's key moves to the visitor's, which asks again by itself.
      return { text: words.other, retry: info.kind !== 'unauthorized' && graphMayResend({ state: { error } }), recheck: false }
  }
}
