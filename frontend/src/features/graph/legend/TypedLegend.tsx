import { useId, useMemo } from 'react'
import type { GraphVocabularies } from '../types'
import type { EdgeMarker } from '../network/channels'
import { own } from '../network/graph'
import type { TypedGraph } from '../network/graph'
import { LABEL_OUTLINE, LABEL_TEXT, NODE_FILL, TESTED_ABSENT_OPACITY } from '../network/palette'
import { legendEntries, lineSwatchWidthPx } from './legendEntries'
import type { LegendSwatch } from './legendEntries'
import { LEGEND_HEADING } from './legendText'

// MARKER_POINTS is drawn in this frame: the 40 by 16 swatch's line runs along y = 8, and
// a marker's tip is at x = 38.
const MID_Y = 8
const TIP_X = 38

// Cytoscape's arrow shapes (src/extensions/renderer/base/arrow-shapes.mjs), tip first,
// scaled to the swatch, so each marker keeps the outline the canvas gives it.
const MARKER_POINTS: Readonly<Record<string, string>> = {
  triangle: '38,8 30.2,4.1 30.2,11.9',
  'vee-hollow': '38,8 30.2,4.1 34.1,8 30.2,11.9',
  tee: '38,4.1 35.4,4.1 35.4,11.9 38,11.9',
  'square-direction': '38,4.1 38,11.9 30.2,11.9 30.2,4.1',
  diamond: '38,8 34.1,4.1 30.2,8 34.1,11.9',
  'chevron-reference': '38,8 34.1,4.1 32.8,5.4 35.4,8 32.8,10.6 34.1,11.9',
}

// Cytoscape draws 'dotted' as [1, 1]; the dashes are TYPED_STYLESHEET's line-dash-pattern.
const DASH_ARRAYS: Readonly<Record<string, string>> = { 'tested-absent': '6 3', predicted: '1 1' }

function EndMarker({ marker, colour }: { marker: EdgeMarker; colour: string }) {
  if (marker === 'circle') return <circle cx={34.1} cy={MID_Y} r={3.9} fill={colour} />
  const points = own(MARKER_POINTS, marker)
  if (points === undefined) return null
  // Hollow means only "effect unknown" (Y4): every other marker is filled.
  return marker === 'vee-hollow'
    ? <polygon points={points} fill="none" stroke={colour} strokeWidth={1} strokeLinejoin="round" />
    : <polygon points={points} fill={colour} />
}

function EdgeSwatch({ marker, line, colour, width }: Extract<LegendSwatch, { kind: 'edge' }>) {
  const stroke = {
    stroke: colour,
    strokeWidth: width ?? lineSwatchWidthPx(line),
    strokeDasharray: own(DASH_ARRAYS, line),
  }
  const end = marker === 'none' ? TIP_X : marker === 'tee' ? 36 : 34
  const middle = line === 'not-significant' || line === 'inconclusive' || line === 'non-support'
  return (
    <g opacity={line === 'tested-absent' ? TESTED_ABSENT_OPACITY : 1}>
      {middle ? (
        <>
          <line x1={2} y1={MID_Y} x2={17} y2={MID_Y} {...stroke} />
          <line x1={23} y1={MID_Y} x2={end} y2={MID_Y} {...stroke} />
          <rect x={17} y={5} width={6} height={6} fill="none" stroke={colour} strokeWidth={1} />
        </>
      ) : (
        <line x1={2} y1={MID_Y} x2={end} y2={MID_Y} {...stroke} />
      )}
      <EndMarker marker={marker} colour={colour} />
    </g>
  )
}

interface ShapePaint { fill: string; stroke: string; strokeWidth: number }

// Rounded joins give the diamond and the triangle the rounded corners the canvas draws.
function NodeShape({ shape, r, paint }: { shape: 'gene' | 'protein' | 'isoform' | 'other'; r: number; paint: ShapePaint }) {
  const x = 20
  const y = MID_Y
  switch (shape) {
    case 'gene':
      return (
        <path
          d={`M ${x - r} ${y - r} H ${x + r} V ${y + r / 2} Q ${x + r} ${y + r} ${x + r / 2} ${y + r} H ${x - r / 2} Q ${x - r} ${y + r} ${x - r} ${y + r / 2} Z`}
          strokeLinejoin="round"
          {...paint}
        />
      )
    case 'protein':
      return <ellipse cx={x} cy={y} rx={r} ry={r} {...paint} />
    case 'isoform':
      return <polygon points={`${x},${y - r} ${x + r},${y} ${x},${y + r} ${x - r},${y}`} strokeLinejoin="round" {...paint} />
    default:
      return <polygon points={`${x},${y - r} ${x + r},${y + r} ${x - r},${y + r}`} strokeLinejoin="round" {...paint} />
  }
}

function NodeSwatch({ shape, seed }: Extract<LegendSwatch, { kind: 'node' }>) {
  if (!seed) return <NodeShape shape={shape} r={6} paint={{ fill: NODE_FILL, stroke: NODE_FILL, strokeWidth: 1.5 }} />
  // The seed's two-tone ring: the light rim outside the dark border, so one of them
  // stands out on either page background.
  return (
    <>
      <NodeShape shape={shape} r={4.5} paint={{ fill: 'none', stroke: LABEL_TEXT, strokeWidth: 7 }} />
      <NodeShape shape={shape} r={4.5} paint={{ fill: 'none', stroke: LABEL_OUTLINE, strokeWidth: 3 }} />
      <NodeShape shape={shape} r={4.5} paint={{ fill: NODE_FILL, stroke: 'none', strokeWidth: 0 }} />
    </>
  )
}

function Swatch({ swatch }: { swatch: LegendSwatch }) {
  return (
    <svg aria-hidden="true" focusable="false" width="40" height="16" style={{ flexShrink: 0, marginTop: 2 }}>
      {swatch.kind === 'edge' && <EdgeSwatch {...swatch} />}
      {swatch.kind === 'node' && <NodeSwatch {...swatch} />}
      {swatch.kind === 'colour' && <rect x={4} y={4} width={32} height={8} fill={swatch.colour} />}
    </svg>
  )
}

interface TypedLegendProps {
  graph: TypedGraph | null
  vocabularies?: GraphVocabularies | null
  showNonSupporting?: boolean
  title?: string
}

/**
 * Each entry's sentence says all its swatch shows, so the swatch is hidden from screen
 * readers, and a text from the vocabularies stays text. The entries come from the graph,
 * never from the canvas, so the legend agrees with what is drawn without waiting for it.
 * No landmark: a page may hold two legends, each under its own title.
 */
export function TypedLegend({ graph, vocabularies, showNonSupporting, title = 'Legend' }: TypedLegendProps) {
  const titleId = useId()
  const entries = useMemo(
    () => legendEntries(graph, { showNonSupporting, vocabularies }),
    [graph, showNonSupporting, vocabularies],
  )
  if (entries.length === 0) return null
  return (
    <div>
      <p id={titleId} style={{ fontWeight: 600, margin: '0 0 4px' }}>{title}</p>
      {entries.some((entry) => entry.key.startsWith('family:')) && <p style={{ margin: '0 0 4px' }}>{LEGEND_HEADING}</p>}
      <ul role="list" aria-labelledby={titleId} style={{ listStyle: 'none', margin: 0, padding: 0 }}>
        {entries.map((entry) => (
          <li key={entry.key} style={{ display: 'flex', alignItems: 'flex-start', gap: 8, margin: '4px 0' }}>
            <Swatch swatch={entry.swatch} />
            {entry.text}
          </li>
        ))}
      </ul>
    </div>
  )
}
