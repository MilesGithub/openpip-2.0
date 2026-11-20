import type { CSSProperties } from 'react'

export const RELEASES_IN_NETWORK = 'Releases in this network'
/** The scroll wrapper around the relationship rows is named here, since their caption has no id to point at. */
export const TABLE_REGION_LABEL = 'Table of relationships in view'

// Inline values, each kept by jsdom, so that the panel wraps below the canvas on a narrow screen and the
// relationship rows scroll sideways, with no media query (J29).
export const WORKSPACE_ROW_STYLE: CSSProperties = { display: 'flex', flexWrap: 'wrap', gap: 16, alignItems: 'flex-start' }
export const MAIN_COLUMN_STYLE: CSSProperties = { flex: '999 1 560px', minWidth: 0 }
export const PANEL_COLUMN_STYLE: CSSProperties = { flex: '1 1 320px', minWidth: 0 }
export const TABLE_REGION_STYLE: CSSProperties = { overflowX: 'auto', marginTop: 12 }
