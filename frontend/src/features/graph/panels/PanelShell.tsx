import { Component, createRef, useEffect, useEffectEvent, useId, useLayoutEffect, useRef } from 'react'
import type { CSSProperties, ReactNode } from 'react'
import { PANEL_LABELS, panelErrorText } from './panelWords'
import type { PanelSubject } from './panelWords'
import { BUTTON_STYLE, HEADING_STYLE, SOURCE_TEXT_STYLE } from './text'
import type { HeadingLevel } from './text'

/**
 * X1's /entities/:id page, where the panel's heading is the page's h1: it takes focus as F4's page headings do (J30),
 * once, on the first render where `ready` holds, after a client-side navigation, while focus is on the body or outside
 * the page's main.
 */
export interface PanelArrival {
  arrived: boolean   // isArrival(location.key): a client-side navigation, never a document's first location
  ready: boolean     // the heading is final: the page's own request has settled
}

export interface PanelShellProps {
  heading: string
  subject: PanelSubject              // PanelBoundary's bad-body words (6; M7)
  bodyKey: string                    // PanelBoundary's key: the body's params key, '' while no body mounts (12.3)
  headingLevel?: HeadingLevel        // default 2
  onClose?: () => void               // Close and Escape only when given (M63)
  headingStyle?: CSSProperties       // X1's /entities/:id page: F4's page heading style in place of HEADING_STYLE
  arrival?: PanelArrival             // X1's /entities/:id page: J30's arrival focus in place of the focus on mount
  children?: ReactNode
}

interface BoundaryProps { subject: PanelSubject; headingId: string; children?: ReactNode }
interface BoundaryState { failed: boolean }

/**
 * Only a class can catch a render error, and nothing else in the app does: a body that passes its
 * guard and still lacks a nested key would otherwise blank the whole page. The shell keys it by the
 * body's params key, so a failure is forgotten when new params mount a new body.
 */
class PanelBoundary extends Component<BoundaryProps, BoundaryState> {
  state: BoundaryState = { failed: false }
  private readonly body = createRef<HTMLDivElement>()

  static getDerivedStateFromError(): BoundaryState {
    return { failed: true }
  }

  componentWillUnmount() {
    // React calls this while the body is still in the document. A remount for new params would
    // otherwise drop focus on the page's body; focus already moved out of the body stays put.
    const body = this.body.current
    if (body !== null && body.contains(document.activeElement)) document.getElementById(this.props.headingId)?.focus()
  }

  render() {
    if (this.state.failed) return <p>{panelErrorText({ kind: 'bad_body', status: 200 }, this.props.subject)}</p>
    // Every element of a waiting view and a body inherits these two properties, so source text
    // keeps its line breaks and long tokens wrap wherever it is rendered.
    return <div ref={this.body} style={SOURCE_TEXT_STYLE}>{this.props.children}</div>
  }
}

export function PanelShell(props: PanelShellProps) {
  const { heading, subject, bodyKey, headingLevel = 2, onClose, headingStyle, arrival, children } = props
  const headingId = useId()
  const sectionRef = useRef<HTMLElement | null>(null)
  const headingRef = useRef<HTMLHeadingElement | null>(null)
  const box = useRef({ focusInside: false })
  // Read through effect events, so the mount effect keeps its empty dependency list (U28).
  const focusOnMount = useEffectEvent(() => arrival === undefined)
  const arrive = useEffectEvent(() => {
    const title = headingRef.current
    const active = document.activeElement
    const page = title?.closest('main') ?? null
    if (arrival?.arrived === true && (active === null || active === document.body || (page !== null && !page.contains(active)))) {
      title?.focus()
    }
  })
  const arrivalReady = arrival?.ready === true
  const arrivalDone = useRef(false)
  useLayoutEffect(() => {
    const section = sectionRef.current
    const state = box.current
    // A layout cleanup runs while the section is still in the document, so it can tell whether focus
    // was inside: a pointer close has already moved focus out, and then focus is left where it went.
    return () => {
      state.focusInside = section !== null && section.contains(document.activeElement)
    }
  }, [])
  useEffect(() => {
    const section = sectionRef.current
    const title = headingRef.current
    const state = box.current
    const active = document.activeElement
    const opener = active instanceof HTMLElement && active !== document.body && !(section !== null && section.contains(active))
      ? active
      : null
    // A row's panel can close because its row left the table, and a list's because its button went.
    let fallback: HTMLElement | null = null
    if (opener !== null) {
      fallback = opener.closest('table')?.querySelector('caption') ?? null
      if (fallback === null) {
        const label = opener.closest('ul')?.getAttribute('aria-labelledby')
        fallback = typeof label === 'string' && label !== '' ? document.getElementById(label) : null
      }
    }
    state.focusInside = false
    if (focusOnMount()) title?.focus()
    return () => {
      if (!state.focusInside) return
      const now = document.activeElement
      const lost = now === null || now === document.body || (section !== null && section.contains(now))
      if (!lost) return
      if (opener !== null && opener.isConnected) opener.focus()
      else if (fallback !== null && fallback.isConnected) fallback.focus()
    }
  }, [])
  useEffect(() => {
    // X1's page h1 (PanelArrival): once, on the first ready render. F3's panels pass no `arrival` and focus on mount.
    if (arrivalReady && !arrivalDone.current) {
      arrivalDone.current = true
      arrive()
    }
  }, [arrivalReady])
  const Heading = `h${headingLevel}` as const
  return (
    <section
      ref={sectionRef}
      aria-labelledby={headingId}
      data-selectable
      onKeyDown={(event) => {
        // With no onClose there is nothing to close, so Escape is left to the page.
        if (onClose !== undefined && event.key === 'Escape') {
          event.stopPropagation()
          onClose()
        }
      }}
      style={{ width: '100%', color: 'var(--text)', borderTop: '1px solid var(--border)', paddingTop: 8 }}
    >
      <Heading id={headingId} ref={headingRef} tabIndex={-1} style={{ ...(headingStyle ?? HEADING_STYLE), ...SOURCE_TEXT_STYLE }}>{heading}</Heading>
      {onClose !== undefined && <button type="button" onClick={onClose} style={BUTTON_STYLE}>{PANEL_LABELS.close}</button>}
      <PanelBoundary key={bodyKey} subject={subject} headingId={headingId}>{children}</PanelBoundary>
    </section>
  )
}
