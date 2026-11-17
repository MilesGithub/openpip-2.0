import { act, cleanup, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { recorded } from '../../../test/graphHooks'
import type { GraphEdge, GraphNetworkResponse, GraphNode } from '../types'
import { graphOfNetwork } from '../network/graph'
import type { TypedGraph } from '../network/graph'
import { useTypedSelection } from './selection'
import type { PanelSelection } from './selection'

// 16.0's console rule: any console.error or console.warn call fails the row.
beforeEach(() => {
  vi.spyOn(console, 'warn')
  vi.spyOn(console, 'error')
})

afterEach(() => {
  cleanup()
  const calls = [...vi.mocked(console.warn).mock.calls, ...vi.mocked(console.error).mock.calls]
  vi.restoreAllMocks()
  expect(calls.map((args) => args.map(String).join(' ').slice(0, 300))).toEqual([])
})

function network(name: string): TypedGraph {
  return graphOfNetwork(recorded<GraphNetworkResponse>(name).body)
}

function edgeOf(graph: TypedGraph, id: string): GraphEdge {
  const edge = graph.edges.find((candidate) => candidate.id === id)
  if (edge === undefined) throw new Error(`no ${id} in the graph`)
  return edge
}

interface Props { graph: TypedGraph | null; show: boolean; initial?: PanelSelection | null }

function hook(initialProps: Props) {
  return renderHook(
    ({ graph, show, initial }: Props) => useTypedSelection(graph, { showNonSupporting: show, initial }),
    { initialProps },
  )
}

describe('useTypedSelection', () => {
  it('SEL-1: a selection made from the graph takes the kept record, clears when hidden and stays cleared', () => {
    const graph = network('network-tf1-outcome-all')
    const record = edgeOf(graph, 'relationship:7')
    // A snapshot that differs from the kept graph's record: the hook reads the record again by id.
    const snapshot: GraphEdge = { ...record, assertion_count: 99 }
    const view = hook({ graph, show: true })
    act(() => view.result.current.select({ kind: 'edge', id: 'relationship:7', record: snapshot }))
    expect(view.result.current.selected).toEqual({ kind: 'edge', id: 'relationship:7', record })
    expect(view.result.current.selected).not.toHaveProperty('params')
    view.rerender({ graph, show: false })
    expect(view.result.current.selected).toBeNull()
    view.rerender({ graph, show: true })
    expect(view.result.current.selected).toBeNull()
  })

  it('SEL-2: a restored selection is kept while graph is null, gains its record, then clears when hidden', () => {
    const graph = network('network-tf1-outcome-all')
    const initial: PanelSelection = { kind: 'edge', id: 'relationship:7' }
    const view = hook({ graph: null, show: true, initial })
    expect(view.result.current.selected).toEqual({ kind: 'edge', id: 'relationship:7' })
    view.rerender({ graph, show: true, initial })
    expect(view.result.current.selected).toEqual({ kind: 'edge', id: 'relationship:7', record: edgeOf(graph, 'relationship:7') })
    view.rerender({ graph, show: false, initial })
    expect(view.result.current.selected).toBeNull()
  })

  it('SEL-3: an entity the graph does not hold, with no params, is kept across a visibility change and a new graph until select(null)', () => {
    const view = hook({ graph: network('network-tf1-outcome-all'), show: true })
    act(() => view.result.current.select({ kind: 'node', id: 'entity:999' }))
    expect(view.result.current.selected).toEqual({ kind: 'node', id: 'entity:999' })
    view.rerender({ graph: network('network-tf1-outcome-all'), show: false })
    expect(view.result.current.selected).toEqual({ kind: 'node', id: 'entity:999' })
    view.rerender({ graph: network('network-tf1'), show: false })
    expect(view.result.current.selected).toEqual({ kind: 'node', id: 'entity:999' })
    act(() => view.result.current.select(null))
    expect(view.result.current.selected).toBeNull()
  })

  it('SEL-4: a selection with params keeps its own record and nodes although the graph holds an edge of that id', () => {
    const graph = network('network-tf1')
    const kept = graph.edges[0]
    const own: GraphEdge = { ...kept, assertion_count: 99 }
    const nodes: GraphNode[] = [{ ...graph.nodes[0], label: 'SYN-OTHER' }]
    const view = hook({ graph, show: false })
    act(() => view.result.current.select({ kind: 'edge', id: kept.id, record: own, params: {}, nodes }))
    expect(view.result.current.selected).toEqual({ kind: 'edge', id: kept.id, record: own, params: {}, nodes })
    view.rerender({ graph: network('network-tf1'), show: true })
    expect(view.result.current.selected).toEqual({ kind: 'edge', id: kept.id, record: own, params: {}, nodes })
  })

  // SEL-5 itself is afterEach's console rule: renderHook mounts one component, so React logs nothing
  // for its own update during render, and a broken guard throws 'Too many re-renders' instead.
  it('SEL-5: after every guarded update, a seen selection is kept while graph is null', () => {
    const all = network('network-tf1-outcome-all')
    const view = hook({ graph: null, show: true, initial: { kind: 'edge', id: 'relationship:7' } })
    // Marked seen, then cleared, then a new selection made and re-resolved on a new graph.
    view.rerender({ graph: all, show: true })
    view.rerender({ graph: all, show: false })
    const seed = all.nodes.find((node) => node.id === 'entity:7')
    if (seed === undefined) throw new Error('no entity:7 in the graph')
    act(() => view.result.current.select({ kind: 'node', id: 'entity:7', record: seed }))
    view.rerender({ graph: network('network-tf1'), show: false })
    view.rerender({ graph: null, show: false })
    expect(view.result.current.selected?.id).toBe('entity:7')
  })

  it('SEL-6: a restored selection that the loaded graph holds and hides is cleared, so no panel opens for it', () => {
    const view = hook({ graph: network('network-tf1-outcome-all'), show: false, initial: { kind: 'edge', id: 'relationship:7' } })
    expect(view.result.current.selected).toBeNull()
  })

  it('SEL-7: a selection made from the graph clears when a new graph lacks it, and stays cleared when that graph returns', () => {
    const all = network('network-tf1-outcome-all')
    const record = edgeOf(all, 'relationship:7')
    const view = hook({ graph: all, show: true })
    act(() => view.result.current.select({ kind: 'edge', id: 'relationship:7', record }))
    expect(view.result.current.selected).toEqual({ kind: 'edge', id: 'relationship:7', record })
    // Absent, not hidden: the new graph does not hold the edge at all.
    const tf1 = network('network-tf1')
    expect(tf1.edges.map((edge) => edge.id)).not.toContain('relationship:7')
    view.rerender({ graph: tf1, show: true })
    expect(view.result.current.selected).toBeNull()
    view.rerender({ graph: all, show: true })
    expect(view.result.current.selected).toBeNull()
  })
})
