import type { StylesheetJson } from 'cytoscape'
import {
  FAMILY_COLOURS, LABEL_OUTLINE, LABEL_TEXT, NEUTRAL, NODE_FILL, SELECTION, TESTED_ABSENT_OPACITY,
} from './palette'

// The last matching block wins (cytoscape's src/style/apply.mjs), so the order is the rule: base,
// family colour, line, marker, hidden, selection. Markers are classes because an enum-typed
// property refuses a 'data(x)' string under tsc, and a marker block sets no colour, so the
// marker takes its line's. Elements carry classes, never colours.

const THIN_GREY_WITH_MIDDLE_SQUARE = {
  width: 1,
  'line-color': NEUTRAL,
  'target-arrow-color': NEUTRAL,
  'mid-target-arrow-shape': 'square',
  'mid-target-arrow-fill': 'hollow',
} as const

export const TYPED_STYLESHEET: StylesheetJson = [
  {
    selector: 'node',
    style: {
      shape: 'ellipse',
      width: 28,
      height: 28,
      'background-color': NODE_FILL,
      'border-width': 0,
      label: 'data(label)',
      'font-size': 11,
      color: LABEL_TEXT,
      'text-outline-color': LABEL_OUTLINE,
      'text-outline-width': 2,
      'text-valign': 'bottom',
      'text-margin-y': 4,
      'min-zoomed-font-size': 7,
    },
  },
  { selector: 'node.sh-gene', style: { shape: 'bottom-round-rectangle' } },
  { selector: 'node.sh-protein', style: { shape: 'ellipse' } },
  { selector: 'node.sh-isoform', style: { shape: 'round-diamond' } },
  { selector: 'node.sh-other', style: { shape: 'round-triangle' } },
  {
    selector: 'node.seed',
    style: {
      'border-width': 3,
      'border-style': 'solid',
      'border-color': LABEL_OUTLINE,
      'outline-width': 2,
      'outline-color': LABEL_TEXT,
      'outline-offset': 0,
    },
  },
  {
    // haystack would draw no target arrow and stack A→B on B→A.
    selector: 'edge',
    style: {
      'curve-style': 'bezier',
      'control-point-step-size': 40,
      'loop-direction': '-45deg',
      'loop-sweep': '-90deg',
      width: 1.5,
      'line-style': 'solid',
      'line-color': NEUTRAL,
      'target-arrow-color': NEUTRAL,
      'mid-target-arrow-color': NEUTRAL,
      'target-arrow-shape': 'none',
      'source-arrow-shape': 'none',
      'mid-target-arrow-shape': 'none',
      'arrow-scale': 1.2,
      label: 'data(label)',
      'font-size': 10,
      color: LABEL_TEXT,
      'text-outline-color': LABEL_OUTLINE,
      'text-outline-width': 2,
      'text-rotation': 'autorotate',
      'min-zoomed-font-size': 8,
      opacity: 1,
    },
  },
  ...Object.entries(FAMILY_COLOURS).map(([family, colour]) => ({
    selector: `edge.fam-${family}`,
    style: { 'line-color': colour, 'target-arrow-color': colour },
  })),
  { selector: 'edge.ln-supported', style: { width: 'mapData(publications, 1, 5, 1.5, 5.5)' } },
  { selector: 'edge.ln-predicted', style: { width: 'mapData(publications, 1, 5, 1.5, 5.5)', 'line-style': 'dotted' } },
  {
    // [6, 3] is Cytoscape 3.33.3's default too; it is stated so a later default cannot change the drawing.
    selector: 'edge.ln-tested-absent',
    style: {
      'line-style': 'dashed',
      'line-dash-pattern': [6, 3],
      width: 1.5,
      opacity: TESTED_ABSENT_OPACITY,
      'line-color': NEUTRAL,
      'target-arrow-color': NEUTRAL,
    },
  },
  { selector: 'edge.ln-not-significant', style: THIN_GREY_WITH_MIDDLE_SQUARE },
  { selector: 'edge.ln-inconclusive', style: THIN_GREY_WITH_MIDDLE_SQUARE },
  { selector: 'edge.ln-non-support', style: THIN_GREY_WITH_MIDDLE_SQUARE },
  { selector: 'edge.ln-reference', style: { width: 1, 'line-color': NEUTRAL, 'target-arrow-color': NEUTRAL } },
  { selector: 'edge.mk-triangle', style: { 'target-arrow-shape': 'triangle', 'target-arrow-fill': 'filled' } },
  { selector: 'edge.mk-tee', style: { 'target-arrow-shape': 'tee' } },
  { selector: 'edge.mk-diamond', style: { 'target-arrow-shape': 'diamond', 'target-arrow-fill': 'filled' } },
  { selector: 'edge.mk-vee-hollow', style: { 'target-arrow-shape': 'vee', 'target-arrow-fill': 'hollow' } },
  { selector: 'edge.mk-circle', style: { 'target-arrow-shape': 'circle', 'target-arrow-fill': 'filled' } },
  { selector: 'edge.mk-square-direction', style: { 'target-arrow-shape': 'square', 'target-arrow-fill': 'filled' } },
  { selector: 'edge.mk-chevron-reference', style: { 'target-arrow-shape': 'chevron', 'target-arrow-fill': 'filled' } },
  { selector: 'edge.mk-none', style: { 'target-arrow-shape': 'none' } },
  { selector: '.hidden', style: { display: 'none' } },
  { selector: '.is-selected', style: { 'underlay-color': SELECTION, 'underlay-opacity': 1, 'underlay-padding': 6 } },
  {
    // The opaque underlay lies under a selected edge's line and its pattern, and no canvas colour
    // has 2:1 against it, so a selected edge is drawn in the label colour. The underlay ends in a
    // 6 px round cap where the line ends, short of the node, so part of an end marker can pass it,
    // where the label colour has 1:1 against the light page and the marker looks cut short: a
    // triangle's tip or a circle's far side from 3 publications, a tee's or diamond's corners from
    // 4, and a square's front corners or the hollow vee outline's corner points at any count.
    // Cytoscape clears a hollow marker through the underlay before stroking it, so on the light
    // page a selected hollow marker reads as filled.
    selector: 'edge.is-selected',
    style: { 'line-color': LABEL_TEXT, 'target-arrow-color': LABEL_TEXT, 'mid-target-arrow-color': LABEL_TEXT },
  },
]
