import { useCallback, useMemo, useState } from 'react'
import type { GraphRelationshipParams } from '../../../api/graph/params'
import type { GraphNode } from '../types'
import { keptGraph } from '../network/graph'
import type { TypedGraph, TypedSelection, TypedSelectionEvent } from '../network/graph'
import { visibleGraph } from '../network/visibility'
import type { VisibleGraph } from '../network/visibility'

/**
 * A selection with or without a record (a URL gives none, PH H6). A panel opened from another
 * panel also carries its params (U33) and the nodes that name its ends (M9).
 */
export type PanelSelection = (TypedSelection | TypedSelectionEvent) & {
  params?: GraphRelationshipParams
  nodes?: readonly GraphNode[]
}
export type SelectionVisibility = 'visible' | 'hidden' | 'absent' | 'unknown'

/** `hidden` and `absent` differ, so a restored selection that the loaded graph holds but hides can be cleared. */
export function selectionVisibility(selection: TypedSelection, visible: VisibleGraph | null): SelectionVisibility {
  if (visible === null) return 'unknown'
  const shown: readonly { id: string }[] = selection.kind === 'edge' ? visible.edges : visible.nodes
  if (shown.some((element) => element.id === selection.id)) return 'visible'
  const hidden: readonly { id: string }[] = selection.kind === 'edge' ? visible.hiddenEdges : visible.hiddenNodes
  return hidden.some((element) => element.id === selection.id) ? 'hidden' : 'absent'
}

/**
 * An event's record is a snapshot, so it is read again from the kept graph by id. A selection with
 * params keeps its own record and nodes: they are the other panel's.
 */
export function resolveSelection(selection: PanelSelection, kept: TypedGraph | null): PanelSelection {
  if (selection.params !== undefined || kept === null) return selection
  if (selection.kind === 'edge') {
    const record = kept.edges.find((edge) => edge.id === selection.id)
    return record === undefined ? selection : { kind: 'edge', id: selection.id, record }
  }
  if (selection.kind === 'node') {
    const record = kept.nodes.find((node) => node.id === selection.id)
    return record === undefined ? selection : { kind: 'node', id: selection.id, record }
  }
  return selection
}

export interface TypedSelectionState {
  selected: PanelSelection | null
  /** The one handler for TypedNetwork, TypedEdgesTable and every panel; null closes. */
  select: (selection: PanelSelection | null) => void
}

interface Held { selection: PanelSelection; seen: boolean }

/**
 * The held selection is marked seen, or cleared, during render under a guard, since a state set in
 * an effect fails lint; the render that finds it cleared already returns null. `initial` is read
 * once, so a selection read from the URL after mount goes through `select`.
 */
export function useTypedSelection(
  graph: TypedGraph | null,
  options: { showNonSupporting: boolean; initial?: PanelSelection | null },
): TypedSelectionState {
  const { showNonSupporting, initial } = options
  const [held, setHeld] = useState<Held | null>(() => (initial === undefined || initial === null ? null : { selection: initial, seen: false }))
  const kept = useMemo(() => (graph === null || graph === undefined ? null : keptGraph(graph).graph), [graph])
  const visible = useMemo(() => (kept === null ? null : visibleGraph(kept, { showNonSupporting })), [kept, showNonSupporting])
  const state: SelectionVisibility = held === null ? 'unknown' : selectionVisibility(held.selection, visible)
  const cleared = held !== null && (
    (state === 'hidden' && (held.seen || held.selection.params === undefined))
    || (state === 'absent' && held.seen)
  )
  if (held !== null && state === 'visible' && !held.seen) setHeld({ ...held, seen: true })
  if (cleared) setHeld(null)
  const select = useCallback((selection: PanelSelection | null) => {
    setHeld(selection === null ? null : { selection, seen: false })
  }, [])
  const selected = held === null || cleared ? null : resolveSelection(held.selection, kept)
  return { selected, select }
}
