import type { GraphFamily } from '../types'
import { own } from './graph'

// A canvas colour has 3:1 against both page backgrounds only at relative luminance 0.113 to
// 0.285. Of Okabe-Ito only blue, bluish green and vermillion #D55E00 are in that band; two are
// used here and vermillion is left free. A family's first type adds its colour from a
// colour-vision-deficiency-validated set, in the band, never SELECTION or NEUTRAL, and passing
// typedStyles.test.ts's contrast test; the PR that adds it compares it with the colours already
// drawn (FAMILY_COLOURS, NEUTRAL, NODE_FILL, SELECTION). Otherwise the family stays grey and is
// told apart by marker and label only (Y10). One palette serves both themes (Y11): the canvas's
// container takes var(--bg), and the colours inside the canvas are concrete.

export const PAGE_BACKGROUNDS = { light: '#fafafa', dark: '#0a0e17' } as const   // index.css:10, 85
export const FAMILY_COLOURS: Partial<Record<GraphFamily, string>> = {
  regulation: '#0072B2',   // Okabe-Ito blue
  physical: '#009E73',     // Okabe-Ito bluish green
}
export const NEUTRAL = '#6B7280'           // reference, non-support, every family without a colour
export const NODE_FILL = '#7C8798'
export const SELECTION = '#AA3377'         // Paul Tol's bright purple
export const LABEL_TEXT = '#F8FAFC'        // also the seed ring's outer rim
export const LABEL_OUTLINE = '#1F2937'     // also the seed ring's border
export const TESTED_ABSENT_OPACITY = 0.85
export const COLOUR_NAMES: Readonly<Record<string, string>> = { '#0072B2': 'Blue', '#009E73': 'Bluish green', '#6B7280': 'Grey' }

/** Grey for a family without a colour, and for a value a deploy brings early. */
export function familyColour(family: GraphFamily): string {
  return own(FAMILY_COLOURS, family) ?? NEUTRAL
}
