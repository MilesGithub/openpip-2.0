import { useEffect, useRef } from 'react'
import { PANEL_LABELS } from './panelWords'
import type { PanelSubject } from './panelWords'
import { BUTTON_STYLE } from './text'

export interface PanelStatusProps {
  subject: PanelSubject              // whose "Try again" (8.3; M16)
  text: string                       // '' when there is nothing to say
  onRetry?: () => void               // given while the owner offers a retry
  busy?: boolean                     // a fetch the button would repeat runs or waits offline: the button stays, aria-disabled (M12)
}

/**
 * The line is mounted with its owner and kept across the owner's states, since a live region that
 * appears together with its first message is not announced. The button sits after it, so a retry's
 * words are announced without the button's name.
 */
export function PanelStatus({ subject, text, onRetry, busy = false }: PanelStatusProps) {
  const paragraphRef = useRef<HTMLParagraphElement | null>(null)
  const focused = useRef(false)
  const offered = onRetry !== undefined
  useEffect(() => {
    if (offered || !focused.current) return
    // The focused button went: left alone, focus would drop on the page's body.
    focused.current = false
    const id = paragraphRef.current?.closest('section')?.getAttribute('aria-labelledby')
    const heading = typeof id === 'string' && id !== '' ? document.getElementById(id) : null
    heading?.focus()
  }, [offered])
  return (
    <>
      <p ref={paragraphRef} role="status" style={{ margin: '4px 0', color: 'var(--text)' }}>{text}</p>
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
          style={busy ? { ...BUTTON_STYLE, cursor: 'default' } : BUTTON_STYLE}
        >
          {PANEL_LABELS.tryAgain[subject]}
        </button>
      )}
    </>
  )
}
