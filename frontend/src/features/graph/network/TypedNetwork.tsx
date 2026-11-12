import { useCallback, useEffect, useEffectEvent, useMemo, useState } from 'react'
import cytoscape from 'cytoscape'
import type { Core, EventObject, SingularElementArgument } from 'cytoscape'
import cola from 'cytoscape-cola'
import CytoscapeComponent from 'react-cytoscapejs'
import type { GraphPresentation, GraphVocabularies } from '../types'
import { EMPTY_GRAPH, keptGraph } from './graph'
import type { KeptGraph, TypedGraph, TypedSelection, TypedSelectionEvent } from './graph'
import { layoutOptions, prefersReducedMotion, stopQuietly } from './layout'
import type { NetworkViewState } from './networkView'
import { resolveLayout } from './presentation'
import type { TypedLayoutName } from './presentation'
import { buildElements, layoutKey } from './typedElements'
import { TYPED_STYLESHEET } from './typedStyles'
import { defaultShowNonSupporting, visibleGraph } from './visibility'
import { networkStatusText, plural } from './words'

// A second registration, as the legacy view also makes, replaces the first without throwing.
cytoscape.use(cola)

export interface TypedNetworkProps {
  graph: TypedGraph | null
  state?: NetworkViewState                 // default 'ready'
  error?: unknown                          // from useTypedNetwork, for the status words
  vocabularies?: GraphVocabularies | null
  presentation?: GraphPresentation | null
  layout?: string | null                   // overrides presentation.layout
  selected?: TypedSelection | null         // controlled
  onSelect?: (selection: TypedSelectionEvent | null) => void
  showNonSupporting?: boolean              // default: defaultShowNonSupporting(graph)
  height?: number                          // px, default 480
  ariaLabel?: string
  onLayoutStop?: () => void                // once per completed run
}

interface NetworkCanvasProps {
  kept: KeptGraph
  show: boolean
  selectedId: string | null
  presentation?: GraphPresentation | null
  vocabularies?: GraphVocabularies | null
  layoutName: TypedLayoutName
  onSelect?: (selection: TypedSelectionEvent | null) => void
  onLayoutStop?: () => void
}

/**
 * The instance is state, so it dies with this component and every effect is keyed on it,
 * StrictMode's discarded instance included; react-cytoscapejs hands it over after every
 * render, and React ignores the same instance.
 */
function NetworkCanvas({
  kept, show, selectedId, presentation, vocabularies, layoutName, onSelect, onLayoutStop,
}: NetworkCanvasProps) {
  const built = useMemo(
    () => buildElements(kept.graph, { showNonSupporting: show, selectedId, presentation, vocabularies }),
    [kept, show, selectedId, presentation, vocabularies],
  )
  const [cy, setCy] = useState<Core | null>(null)
  const handleCy = useCallback((instance: Core) => setCy(instance), [])
  // The ids and the layout's name, and nothing else: a selection, a visibility toggle or an
  // equal refetch never moves a node.
  const key = layoutKey(kept.graph, layoutName)

  // cose and cola both emit layoutstop when stopped, so only a run that ends by itself
  // calls onLayoutStop. A stopped frame-sliced cose run still runs one more frame, which
  // stopQuietly makes write nothing: the core may already be destroyed, or the next run
  // already placed.
  const notifyStop = useEffectEvent(() => onLayoutStop?.())
  useEffect(() => {
    if (!cy || cy.destroyed()) return
    const run = cy.layout(layoutOptions(layoutName, { reducedMotion: prefersReducedMotion() }))
    let current = true
    const onStop = () => {
      if (current) notifyStop()
    }
    run.one('layoutstop', onStop)
    // react-cytoscapejs destroys the core in componentWillUnmount, inside the commit, while
    // React runs this cleanup in a later task after an update that is not synchronous, so a
    // frame of the run can fall between the two. Listening before run() also puts this
    // ahead of cola's own destroy handler.
    const onDestroy = () => {
      current = false
      run.off('layoutstop', onStop)
      stopQuietly(cy, run)
    }
    cy.one('destroy', onDestroy)
    run.run()
    return () => {
      current = false
      run.off('layoutstop', onStop)
      cy.off('destroy', onDestroy)
      stopQuietly(cy, run)
    }
  }, [cy, key, layoutName])

  // A tap on an element bubbles to the core with the element as its target, so one core
  // listener sees them all. The ids are opaque refs: a '#' selector would misread
  // `entity:7`, so they are looked up in the builder's maps.
  const handleTap = useEffectEvent((evt: EventObject) => {
    if (evt.target === cy) {
      onSelect?.(null)
      return
    }
    const target = evt.target as SingularElementArgument
    if (target.hasClass('hidden')) return
    const id = target.id()
    const node = built.nodes.get(id)
    if (node !== undefined) {
      onSelect?.({ kind: 'node', id, record: node })
      return
    }
    const edge = built.edges.get(id)
    if (edge !== undefined) onSelect?.({ kind: 'edge', id, record: edge })
  })
  useEffect(() => {
    if (!cy || cy.destroyed()) return
    const listener = (evt: EventObject) => handleTap(evt)
    cy.on('tap', listener)
    // By reference: removeAllListeners would also remove cola's.
    return () => {
      cy.off('tap', listener)
    }
  }, [cy])

  // autounselectify keeps Cytoscape's own selection off: the selection drawn is the
  // `selected` prop's, as a class, which survives the component's class patches.
  return (
    <CytoscapeComponent
      elements={built.elements}
      stylesheet={TYPED_STYLESHEET}
      cy={handleCy}
      style={{ width: '100%', height: '100%' }}
      autounselectify
      boxSelectionEnabled={false}
      minZoom={0.2}
      maxZoom={3}
    />
  )
}

/**
 * The typed graph's network. The canvas is pointer-only: nothing in it is focusable and
 * no key acts on it. Its keyboard and screen-reader equivalent is F3's edges table, which
 * a page mounts beside it (architecture §9, Accessibility). The status line is one node in
 * every state, outside the canvas's image, so each change is announced.
 */
export function TypedNetwork(props: TypedNetworkProps) {
  const {
    graph, state = 'ready', error, vocabularies, presentation, layout, selected, onSelect, showNonSupporting,
    height = 480, ariaLabel, onLayoutStop,
  } = props
  const kept = useMemo(() => keptGraph(graph ?? EMPTY_GRAPH), [graph])
  const show = showNonSupporting ?? defaultShowNonSupporting(graph)
  const visible = useMemo(() => visibleGraph(kept.graph, { showNonSupporting: show }), [kept, show])
  const selectedId = selected ? selected.id : null
  const layoutName = resolveLayout(layout ?? presentation?.layout)
  const status = networkStatusText(state, { error, hasGraph: graph !== null, empty: visible.nodes.length === 0 })
  const label = ariaLabel
    ?? `Network of ${plural(visible.nodes.length, '1 entity', '{n} entities')} and ${plural(visible.edges.length, '1 relationship', '{n} relationships')}.`
  return (
    <>
      <p role="status" style={{ color: 'var(--text-muted)', fontSize: 14, margin: '0 0 4px', minHeight: '1.4em' }}>
        {status}
      </p>
      {graph !== null && visible.nodes.length > 0 && (
        <div role="img" aria-label={label} style={{ width: '100%', height, background: 'var(--bg)' }}>
          <NetworkCanvas
            kept={kept}
            show={show}
            selectedId={selectedId}
            presentation={presentation}
            vocabularies={vocabularies}
            layoutName={layoutName}
            onSelect={onSelect}
            onLayoutStop={onLayoutStop}
          />
        </div>
      )}
    </>
  )
}
