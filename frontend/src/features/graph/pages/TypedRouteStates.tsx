import { useEffect, useEffectEvent, useRef } from 'react'
import type { ReactNode, RefObject } from 'react'
import { useLocation } from 'react-router-dom'
import { isArrival } from './pageHooks'
import { PAGE_BUTTON_OFF_STYLE, PAGE_BUTTON_STYLE, PAGE_HEADING_STYLE, PAGE_STYLE, STATUS_LINE_STYLE } from './pageStyle'
import { PAGE_LOADING, PAGE_UNKNOWN, RELOAD, TRY_AGAIN } from './pageWords'
import { reloadDocument } from './reloadDocument'

export interface PageHeadingProps {
  headingRef: RefObject<HTMLHeadingElement | null>
  children: ReactNode
  ready?: boolean                               // default true; false while the state is transient, so this heading takes no arrival focus
}
export function PageHeading(props: PageHeadingProps) {
  const { headingRef, children, ready = true } = props
  // One rule for every page state (J30): after a client-side navigation the heading takes focus once, unless
  // a control inside the page holds it; a link outside the page's `main`, as the Navbar's is, hands focus on.
  // The location is read through an effect event, so the effect runs once, on the first ready render.
  const arrived = isArrival(useLocation().key)
  const done = useRef(false)
  const arrive = useEffectEvent(() => {
    const heading = headingRef.current
    const active = document.activeElement
    const page = heading?.closest('main') ?? null
    if (arrived && (active === null || active === document.body || (page !== null && !page.contains(active)))) heading?.focus()
  })
  useEffect(() => {
    if (ready && !done.current) {
      done.current = true
      arrive()
    }
  }, [ready])
  return <h1 ref={headingRef} tabIndex={-1} style={PAGE_HEADING_STYLE}>{children}</h1>
}

export interface TypedRouteStatusProps {
  text: string                                  // '' when there is nothing to say
  onRetry?: () => void                          // given while a "Try again" is offered
  busy?: boolean                                // the retry's fetch runs or waits offline: aria-disabled
  focusOnGone?: RefObject<HTMLElement | null>   // where focus goes when the focused "Try again" goes; default this line
}
export function TypedRouteStatus(props: TypedRouteStatusProps) {
  const { text, onRetry, busy = false, focusOnGone } = props
  // As F3's PanelStatus: the line is mounted with its owner and kept across the owner's states, since a live
  // region that appears together with its first message is not announced.
  const lineRef = useRef<HTMLParagraphElement | null>(null)
  const focused = useRef(false)
  const offered = onRetry !== undefined
  useEffect(() => {
    if (offered || !focused.current) return
    // The focused button went: left alone, focus would drop on the page's body.
    focused.current = false
    const target = focusOnGone?.current ?? lineRef.current
    target?.focus()
  }, [offered, focusOnGone])
  return (
    <>
      <p ref={lineRef} role="status" tabIndex={-1} style={STATUS_LINE_STYLE}>{text}</p>
      {onRetry !== undefined && (
        <button
          type="button"
          // Never `disabled`: a browser takes focus from a focused control that becomes disabled.
          aria-disabled={busy ? 'true' : undefined}
          onFocus={() => {
            focused.current = true
          }}
          onBlur={() => {
            focused.current = false
          }}
          onClick={() => {
            if (busy) return
            onRetry()
          }}
          style={busy ? PAGE_BUTTON_OFF_STYLE : PAGE_BUTTON_STYLE}
        >
          {TRY_AGAIN}
        </button>
      )}
    </>
  )
}

export interface TypedRouteLoadingProps { title: string }
export function TypedRouteLoading(props: TypedRouteLoadingProps) {
  const { title } = props
  // The Suspense fallback carries the page's one `h1` while its chunk loads; the state is transient, so the
  // heading takes no arrival focus and the page's own heading takes it once (J43).
  const headingRef = useRef<HTMLHeadingElement | null>(null)
  return (
    <div style={PAGE_STYLE}>
      <PageHeading headingRef={headingRef} ready={false}>{title}</PageHeading>
      <p role="status" style={STATUS_LINE_STYLE}>{PAGE_LOADING}</p>
    </div>
  )
}

export function TypedRouteError() {
  // React.lazy keeps a failed import until the document reloads, so the remedy is a reload, never a retry of
  // the import; nothing of the error is shown (J38).
  const headingRef = useRef<HTMLHeadingElement | null>(null)
  return (
    <div style={PAGE_STYLE}>
      <PageHeading headingRef={headingRef}>{PAGE_UNKNOWN}</PageHeading>
      <button type="button" onClick={() => reloadDocument()} style={PAGE_BUTTON_STYLE}>{RELOAD}</button>
    </div>
  )
}
