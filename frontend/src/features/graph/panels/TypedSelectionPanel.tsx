import { useMemo, useState } from 'react'
import type { GraphNetworkQuery, GraphPresentation, GraphVocabularies } from '../types'
import { EMPTY_GRAPH, keptGraph } from '../network/graph'
import type { TypedGraph } from '../network/graph'
import { EntityPanel } from './EntityPanel'
import { relationshipParamsOf, selectionFromParams, selectionOf } from './panelParams'
import { RelationshipPanel } from './RelationshipPanel'
import type { PanelSelection } from './selection'
import type { HeadingLevel } from './text'

export interface TypedSelectionPanelProps {
  selection: PanelSelection                       // useTypedSelection().selected; the page mounts this only while it is not null
  graph: TypedGraph | null                        // the same object TypedNetwork gets
  vocabularies?: GraphVocabularies | null
  presentation?: GraphPresentation | null
  onSelect?: (selection: PanelSelection | null) => void   // useTypedSelection().select
  onClose?: () => void                            // () => select(null)
  headingLevel?: HeadingLevel                     // default 2
}

interface HeldQuery { query: GraphNetworkQuery; graph: TypedGraph }

/**
 * The panels are built from the last query the network answered, never from a null graph: when a
 * request under new params fails or the network is switched off, an open panel keeps its content and
 * sends nothing, where converting the null would re-ask with no filter and the API's default
 * releases. Before any query, a restored selection mounts no body, so it sends nothing either.
 */
export function TypedSelectionPanel(props: TypedSelectionPanelProps) {
  const { selection, graph, vocabularies, presentation, onSelect, onClose, headingLevel = 2 } = props
  const [held, setHeld] = useState<HeldQuery | null>(() => (
    graph !== null && graph !== undefined && graph.query !== null ? { query: graph.query, graph } : null
  ))
  // A guarded update during render, since a state set in an effect fails lint. A refetch's new graph
  // replaces the held one under the same query, since its names may differ.
  let current = held
  if (graph !== null && graph !== undefined && graph.query !== null && graph !== held?.graph) {
    current = { query: graph.query, graph }
    setHeld(current)
  }
  // The panel names its ends, and finds a twin, from this graph, so a null graph would turn its
  // heading to refs.
  const shownGraph = graph ?? current?.graph ?? null
  const kept = useMemo(() => keptGraph(shownGraph ?? EMPTY_GRAPH).graph, [shownGraph])
  if (selection.kind === 'edge') {
    const params = selection.params ?? (current === null ? null : relationshipParamsOf(current.query))
    const edge = 'record' in selection ? selection.record : (kept.edges.find((candidate) => candidate.id === selection.id) ?? null)
    return (
      <RelationshipPanel
        key={'edge:' + selection.id}
        relationshipRef={selection.id}
        params={params}
        edge={edge}
        graph={shownGraph}
        nodes={selection.nodes}
        vocabularies={vocabularies}
        presentation={presentation}
        onSelect={onSelect}
        onClose={onClose}
        headingLevel={headingLevel}
      />
    )
  }
  if (selection.kind === 'node') {
    const entitySelection = selection.params !== undefined
      ? selectionFromParams(selection.params)
      : (current === null ? null : selectionOf(current.query))
    const node = 'record' in selection ? selection.record : (kept.nodes.find((candidate) => candidate.id === selection.id) ?? null)
    return (
      <EntityPanel
        key={'node:' + selection.id}
        entityRef={selection.id}
        selection={entitySelection}
        node={node}
        vocabularies={vocabularies}
        presentation={presentation}
        onSelect={onSelect}
        onClose={onClose}
        headingLevel={headingLevel}
      />
    )
  }
  return null
}
