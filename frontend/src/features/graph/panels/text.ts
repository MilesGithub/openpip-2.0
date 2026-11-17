import type { CSSProperties } from 'react'
import { plural } from '../network/words'

export type HeadingLevel = 1 | 2 | 3 | 4 | 5 | 6

/** A record rather than arithmetic, so the result is a HeadingLevel with no cast. */
const NEXT_LEVEL: Readonly<Record<HeadingLevel, HeadingLevel>> = { 1: 2, 2: 3, 3: 4, 4: 5, 5: 6, 6: 6 }

/** level + 1, at most 6. */
export function subLevel(level: HeadingLevel): HeadingLevel {
  return NEXT_LEVEL[level]
}

export const RAW_VALUE_MAX_CHARS = 1000
/** The API's page cap counts characters, and one record may hold any number of columns. */
export const RAW_COLUMNS_MAX = 200

/** An open object's values never reach JSX any other way: an object child throws, and a spread injects props. */
export function sourceText(value: unknown): string {
  if (typeof value === 'string') return value
  if (typeof value === 'number' || typeof value === 'boolean' || typeof value === 'bigint' || value === null) return String(value)
  if (value === undefined) return ''
  try {
    const json: unknown = JSON.stringify(value)
    return typeof json === 'string' ? json : String(value)
  } catch {
    return String(value)
  }
}

/**
 * A length guard for every source string the panels and the table render, so a 10,000-character
 * label cannot fill a heading, a cell or a button name. A cut never leaves a lone high surrogate.
 */
export function clipText(text: string, max = RAW_VALUE_MAX_CHARS): string {
  if (text.length <= max) return text
  const last = text.charCodeAt(max - 1)
  const kept = last >= 0xd800 && last <= 0xdbff ? max - 1 : max
  const rest = text.length - kept
  return `${text.slice(0, kept)}…${plural(rest, ' (1 more character not shown)', ' ({n} more characters not shown)')}`
}

/** React rewrites a `javascript:` href instead of dropping it, so only these values ever reach `href`. */
export function isHttpUrl(value: unknown): value is string {
  return typeof value === 'string' && /^https?:\/\//i.test(value)
}

/** A body can pass its guard and still lack a list. */
export function arrayOf<T>(value: readonly T[] | null | undefined): readonly T[] {
  return Array.isArray(value) ? value : []
}

// Tailwind's preflight resets what the panels and the table rely on, so each element takes its look
// from these constants.
export const SOURCE_TEXT_STYLE: CSSProperties = { whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }
export const HEADING_STYLE: CSSProperties = { margin: '0 0 4px', fontSize: 16, fontWeight: 600 }
export const SUBHEADING_STYLE: CSSProperties = { margin: '12px 0 4px', fontSize: 14, fontWeight: 600 }
export const TERM_STYLE: CSSProperties = { fontWeight: 600 }
export const DETAIL_STYLE: CSSProperties = { margin: '0 0 6px' }
export const CELL_STYLE: CSSProperties = {
  padding: '4px 8px', textAlign: 'left', verticalAlign: 'top', borderBottom: '1px solid var(--border)',
}
/** Its `--text-muted` edge, not its fill, marks the control at 3:1 or more. */
export const BUTTON_STYLE: CSSProperties = {
  border: '1px solid var(--text-muted)', borderRadius: 6, padding: '2px 8px', background: 'var(--surface)', color: 'var(--text)',
  cursor: 'pointer',
}
/** The row, end, entity and reference buttons read as links. */
export const LINK_BUTTON_STYLE: CSSProperties = { color: 'var(--text)', textDecoration: 'underline', cursor: 'pointer', textAlign: 'left' }
/** Never `--primary`, which an admin sets at run time, and never the browser's link colour. */
export const LINK_STYLE: CSSProperties = { color: 'var(--text)', textDecoration: 'underline' }
