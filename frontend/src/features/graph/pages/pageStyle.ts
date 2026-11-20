import type { CSSProperties } from 'react'

// Inline values with CSS variables, each kept by jsdom (J32). The first JavaScript of every page holds
// this module, so it imports no F3 style either (J13).

export const PAGE_STYLE: CSSProperties = { width: '100%', maxWidth: 1280, margin: '0 auto', padding: '32px 16px 64px', boxSizing: 'border-box' }
/** 72 px keeps a heading that takes focus clear of the Layout's 56 px header. */
export const PAGE_HEADING_STYLE: CSSProperties = { margin: '0 0 12px', fontSize: 28, fontWeight: 600, scrollMarginTop: 72 }
export const SECTION_HEADING_STYLE: CSSProperties = { margin: '24px 0 8px', fontSize: 18, fontWeight: 600 }
export const STATE_LINE_STYLE: CSSProperties = { margin: '4px 0', color: 'var(--text-muted)' }
// The seed line, the plain lists and the cards hold names, titles and addresses from the source, any of which can be
// one run of characters wider than the page: `anywhere` breaks it, and of the values that break inside a word only it
// lets a button sized to its text narrow (CSS Text 3).
export const STATUS_LINE_STYLE: CSSProperties = { margin: '8px 0', minHeight: '1.4em', color: 'var(--text)', overflowWrap: 'anywhere' }
export const PLAIN_LIST_STYLE: CSSProperties = { listStyle: 'none', margin: 0, padding: 0, overflowWrap: 'anywhere' }
export const CARD_LIST_STYLE: CSSProperties = {
  listStyle: 'none', margin: 0, padding: 0, display: 'grid', gap: 16, gridTemplateColumns: 'repeat(auto-fill, minmax(min(100%, 280px), 1fr))',
}
/** The edge is decoration: the state is a word. */
export const CARD_STYLE: CSSProperties = {
  minWidth: 0, padding: 16, border: '1px solid var(--border)', borderRadius: 10, background: 'var(--surface)', overflowWrap: 'anywhere',
}
export const FORM_ROW_STYLE: CSSProperties = { display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'flex-end', margin: '12px 0' }
export const FIELD_STYLE: CSSProperties = { display: 'flex', flexDirection: 'column', gap: 4, minWidth: 0 }
export const INPUT_STYLE: CSSProperties = {
  maxWidth: '100%', padding: '4px 8px', border: '1px solid var(--text-muted)', borderRadius: 6, background: 'var(--surface)', color: 'var(--text)',
}
/**
 * F3's BUTTON_STYLE values (panels/text.ts), its `border` written as three longhands: the off style
 * changes only `borderStyle`, and beside the shorthand React would clear it and leave no edge.
 */
export const PAGE_BUTTON_STYLE: CSSProperties = {
  borderWidth: 1, borderStyle: 'solid', borderColor: 'var(--text-muted)', borderRadius: 6, padding: '2px 8px', background: 'var(--surface)',
  color: 'var(--text)', cursor: 'pointer',
}
/** An `aria-disabled` button, off or busy: its dashed `--text-muted` edge still reads as a control, never by opacity. */
export const PAGE_BUTTON_OFF_STYLE: CSSProperties = { ...PAGE_BUTTON_STYLE, borderStyle: 'dashed', cursor: 'default' }
export const CARD_TITLE_STYLE: CSSProperties = { margin: '0 0 4px', fontSize: 18, fontWeight: 600 }
