import type { CSSProperties } from 'react'

export const SYNTHETIC_BADGE = 'Synthetic demonstration data'
/**
 * The instance header's sentence after the badge, in the same paragraph, so the badge's words are read
 * once: F2's LEGEND_TEXT.synthetic starts with them, and a test keeps the two in step (J21).
 */
export const SYNTHETIC_EXPLAINED = 'Made up to demonstrate openPIP; it describes no real biology.'
/** Its `--text-muted` edge reads at 3:1 or more on `--surface` and `--bg` in both themes; the words carry the meaning. */
export const BADGE_STYLE: CSSProperties = {
  display: 'inline-block', padding: '1px 8px', border: '1px solid var(--text-muted)', borderRadius: 6, fontSize: 12, fontWeight: 600,
  color: 'var(--text)', background: 'var(--surface)',
}
