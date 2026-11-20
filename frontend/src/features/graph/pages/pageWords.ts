// The first JavaScript of every page holds this module, which App.tsx imports through typedRoutes.ts,
// so it imports no F3 module: SERVER_UNREACHABLE and throttledText restate F3's words for `network`
// and `throttled` (panels/panelWords.ts), with their own plural, and a test pins them to F3's (J13).
// X1's and C1's labels, titles and off sentences join the Browsers words at the end.

export const PAGE_LOADING = 'Loading this page…'
export const PAGE_UNKNOWN = 'This page could not be loaded.'
export const TRY_AGAIN = 'Try again'
export const RELOAD = 'Reload'
export const SERVER_UNREACHABLE = 'The server could not be reached.'

export function throttledText(retryAfter: number | null): string {
  if (typeof retryAfter !== 'number') return 'Too many requests. Try again shortly.'
  return retryAfter === 1 ? 'Too many requests. Try again in 1 second.' : `Too many requests. Try again in ${retryAfter} seconds.`
}

export const BROWSERS_NAV_LABEL = 'Browsers'
export const BROWSERS_TITLE = 'Browsers'
export const BROWSERS_NOT_ENABLED = 'Browsers are not enabled on this portal.'
