import { act, cleanup, render, waitFor } from '@testing-library/react'
import { AxiosError } from 'axios'
import cytoscape from 'cytoscape'
import type { Core, LayoutOptions, Position } from 'cytoscape'
import { StrictMode, useEffect, useLayoutEffect, useState } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Mock } from 'vitest'
import { makeNetworkResponse, ppiEdge, ppiNodes, presentationAllKeys } from '../../../mocks/fixtures/typedGraph/extras'
import { recorded } from '../../../test/graphHooks'
import type { GraphEdge, GraphNetworkResponse } from '../types'
import { graphOfNetwork } from './graph'
import type { TypedGraph, TypedGraphNode, TypedSelection, TypedSelectionEvent } from './graph'
import { layoutOptions, stopQuietly } from './layout'
import { TypedNetwork } from './TypedNetwork'
import { buildElements } from './typedElements'
import { TYPED_STYLESHEET } from './typedStyles'

const log = vi.hoisted(() => ({ instances: [], runs: new Map() }))
vi.mock('react-cytoscapejs', async (importOriginal) =>
  (await import('../../../test/headlessCytoscape')).headlessCytoscapeModule(importOriginal, log))

// Cytoscape and React report a bad value or an unwrapped update through the console instead of throwing.
const REPORTED = /cytoscape|is invalid|not a valid|act\(/i

beforeEach(() => {
  log.instances.length = 0
  log.runs.clear()
  vi.spyOn(console, 'warn')
  vi.spyOn(console, 'error')
})

afterEach(() => {
  // Unmounted here rather than after this hook, so that what the unmount reports is checked too.
  cleanup()
  vi.unstubAllGlobals()
  const calls = [...vi.mocked(console.warn).mock.calls, ...vi.mocked(console.error).mock.calls]
  vi.restoreAllMocks()
  expect(calls.map((args) => args.map(String).join(' ')).filter((text) => REPORTED.test(text))).toEqual([])
})

function body(name: string): GraphNetworkResponse {
  return recorded<GraphNetworkResponse>(name).body
}

function network(name: string): TypedGraph {
  return graphOfNetwork(body(name))
}

function edgeOf(graph: TypedGraph, id: string): GraphEdge {
  const edge = graph.edges.find((candidate) => candidate.id === id)
  if (edge === undefined) throw new Error(`no ${id} in the graph`)
  return edge
}

function nodeOf(graph: TypedGraph, id: string): TypedGraphNode {
  const node = graph.nodes.find((candidate) => candidate.id === id)
  if (node === undefined) throw new Error(`no ${id} in the graph`)
  return node
}

function instances(): Core[] {
  return log.instances as Core[]
}

function runs(cy: Core): number {
  return (log.runs as Map<Core, number>).get(cy) ?? 0
}

/** The one instance alive, so that a row never reads a destroyed one by mistake. */
function only(): Core {
  const live = instances().filter((cy) => !cy.destroyed())
  expect(live).toHaveLength(1)
  return live[0]
}

function positions(cy: Core): Position[] {
  return cy.nodes().map((node) => ({ ...node.position() }))
}

function moved(before: Position[], after: Position[]): number {
  return after.filter((position, index) => position.x !== before[index].x || position.y !== before[index].y).length
}

/** Inside act, so that whatever settles meanwhile is flushed where React expects it. */
async function pause(ms: number): Promise<void> {
  await act(async () => {
    await new Promise<void>((resolve) => setTimeout(resolve, ms))
  })
}

function reduceMotion(): void {
  vi.stubGlobal('matchMedia', (query: string) => ({ matches: query === '(prefers-reduced-motion: reduce)', media: query }))
}

interface LayoutCall { cy: Core; destroyed: boolean; options: LayoutOptions }

/** Whether the instance was destroyed is read at the call, since it changes later; the constructor's own 'null' layout is no run. */
function recordLayouts(): LayoutCall[] {
  const calls: LayoutCall[] = []
  const probe = cytoscape({ headless: true })
  const core = Object.getPrototypeOf(probe) as Core
  probe.destroy()
  const original = core.layout
  vi.spyOn(core, 'layout').mockImplementation(function (this: Core, options: LayoutOptions) {
    if (options.name !== 'null') calls.push({ cy: this, destroyed: this.destroyed(), options })
    return original.call(this, options)
  })
  return calls
}

/**
 * Each read is a turn of its own, and the frame that ends a run both places the nodes and
 * calls onLayoutStop, so no read can fall between the two.
 */
async function positionsUntilStop(cy: Core, onLayoutStop: Mock): Promise<Position[][]> {
  const reads: Position[][] = []
  for (let waited = 0; onLayoutStop.mock.calls.length === 0; waited += 20) {
    if (waited > 8000) throw new Error('the layout run did not end')
    reads.push(positions(cy))
    await pause(20)
  }
  return reads
}

const json503 = () =>
  new AxiosError('x', undefined, undefined, undefined, {
    status: 503, statusText: '', headers: {}, config: {} as never, data: { detail: 'Too expensive.' },
  })

/** Longer than one of jsdom's 16.7 ms frames, so that a frame falls due inside the commit that mounts it. */
function SlowCommit() {
  useLayoutEffect(() => {
    const end = performance.now() + 20
    while (performance.now() < end) {
      // The next view takes this long to commit.
    }
  }, [])
  return null
}

interface DroppingPageProps { layout?: string; onLayoutStop: () => void; onReady: (drop: () => void) => void }

/** A page that drops its network in a plain update, as a route change or new data does, for a view that commits slowly. */
function DroppingPage({ layout, onLayoutStop, onReady }: DroppingPageProps) {
  const [graph, setGraph] = useState<TypedGraph | null>(() => network('network-all-edges'))
  useEffect(() => {
    onReady(() => setGraph(null))
  }, [onReady])
  return (
    <>
      <TypedNetwork graph={graph} layout={layout} onLayoutStop={onLayoutStop} />
      {graph === null && <SlowCommit />}
    </>
  )
}

describe('layout runs per instance', () => {
  it('runs once at mount, and again only for new ids or a new layout name', () => {
    const onSelect = vi.fn<(selection: TypedSelectionEvent | null) => void>()
    const tf1 = network('network-tf1')
    const selected: TypedSelection = { kind: 'edge', id: 'relationship:6' }
    const view = render(<TypedNetwork graph={tf1} onSelect={onSelect} />)
    const cy = only()
    expect(runs(cy)).toBe(1)
    // Cytoscape's own selection stays off, so the selection drawn is the prop's alone.
    expect([cy.autounselectify(), cy.boxSelectionEnabled()]).toEqual([true, false])

    act(() => {
      cy.$id('entity:7').emit('tap')
    })
    act(() => {
      cy.$id('relationship:5').emit('tap')
    })
    act(() => {
      cy.emit('tap')
    })
    expect(onSelect.mock.calls.map(([selection]) => (selection === null ? null : selection.id))).toEqual([
      'entity:7', 'relationship:5', null,
    ])
    expect(runs(cy)).toBe(1)

    view.rerender(<TypedNetwork graph={tf1} onSelect={onSelect} selected={selected} />)
    expect(cy.$id('relationship:6').hasClass('is-selected')).toBe(true)
    expect(runs(cy)).toBe(1)
    view.rerender(<TypedNetwork graph={tf1} onSelect={onSelect} selected={selected} showNonSupporting />)
    view.rerender(<TypedNetwork graph={tf1} onSelect={onSelect} selected={selected} showNonSupporting={false} />)
    expect(runs(cy)).toBe(1)
    view.rerender(<TypedNetwork graph={tf1} onSelect={onSelect} selected={selected} showNonSupporting={false} />)
    const refetched = graphOfNetwork(JSON.parse(JSON.stringify(body('network-tf1'))) as GraphNetworkResponse)
    view.rerender(<TypedNetwork graph={refetched} onSelect={onSelect} selected={selected} showNonSupporting={false} />)
    expect(runs(cy)).toBe(1)

    // The same ids with new data: relationship:6 activates in release 1.
    view.rerender(<TypedNetwork graph={network('network-tf1-v1')} onSelect={onSelect} selected={selected} showNonSupporting={false} />)
    expect(cy.$id('relationship:6').hasClass('mk-triangle')).toBe(true)
    expect(runs(cy)).toBe(1)

    const outcomeAll = network('network-tf1-outcome-all')
    view.rerender(<TypedNetwork graph={outcomeAll} onSelect={onSelect} selected={selected} showNonSupporting={false} />)
    expect(cy.$id('relationship:7').length).toBe(1)
    expect(runs(cy)).toBe(2)
    // cola must be registered, or this layout name throws.
    expect(() =>
      view.rerender(
        <TypedNetwork graph={outcomeAll} onSelect={onSelect} selected={selected} showNonSupporting={false} layout="cola" />,
      ),
    ).not.toThrow()
    expect(runs(cy)).toBe(3)
    expect(instances()).toHaveLength(1)
    expect(instances()[0]).toBe(cy)
  })

  it('under StrictMode, runs once on the instance that survives and never on the discarded one', () => {
    render(
      <StrictMode>
        <TypedNetwork graph={network('network-tf1')} />
      </StrictMode>,
    )
    expect(instances().filter((cy) => cy.destroyed()).map(runs)).toEqual([0])
    expect(instances().filter((cy) => !cy.destroyed()).map(runs)).toEqual([1])
  })

  it("takes the presentation's layout unless the layout prop names one, and labels the nodes through the presentation", () => {
    const calls = recordLayouts()
    const tf1 = network('network-tf1')
    const view = render(<TypedNetwork graph={tf1} presentation={{ layout: 'grid' }} />)
    view.rerender(<TypedNetwork graph={tf1} presentation={{ layout: 'grid' }} layout="circle" />)
    expect(calls.map((call) => call.options.name)).toEqual(['grid', 'circle'])
    view.unmount()
    render(<TypedNetwork graph={network('network-all-edges')} presentation={presentationAllKeys} />)
    expect(only().$id('entity:3').data('label')).toBe('SYN-G3')
  })
})

describe('onLayoutStop', () => {
  it('is called once for the run at mount, and not for a selection', async () => {
    const onLayoutStop = vi.fn()
    const tf1 = network('network-tf1')
    const view = render(<TypedNetwork graph={tf1} onLayoutStop={onLayoutStop} />)
    await waitFor(() => expect(onLayoutStop).toHaveBeenCalledTimes(1), { timeout: 8000 })
    view.rerender(<TypedNetwork graph={tf1} onLayoutStop={onLayoutStop} selected={{ kind: 'node', id: 'entity:7' }} />)
    await pause(1000)
    expect(onLayoutStop).toHaveBeenCalledTimes(1)
    expect(runs(only())).toBe(1)
  })

  it('is never called for a run stopped by a key change', async () => {
    const onLayoutStop = vi.fn()
    const view = render(<TypedNetwork graph={network('network-tf1')} onLayoutStop={onLayoutStop} />)
    view.rerender(<TypedNetwork graph={network('network-tf1-outcome-all')} onLayoutStop={onLayoutStop} />)
    expect(runs(only())).toBe(2)
    await waitFor(() => expect(onLayoutStop).toHaveBeenCalledTimes(1), { timeout: 8000 })
    // Long enough for the first run to have ended twice over, had it not been stopped.
    await pause(1000)
    expect(onLayoutStop).toHaveBeenCalledTimes(1)
  })

  it('is called inside run() for grid under the reduced-motion stub, so a row need not wait for it', () => {
    reduceMotion()
    const onLayoutStop = vi.fn()
    render(<TypedNetwork graph={network('network-tf1')} layout="grid" onLayoutStop={onLayoutStop} />)
    expect(onLayoutStop).toHaveBeenCalledTimes(1)
  })
})

describe('a run that is stopped writes nothing afterwards', () => {
  it('unmounted during a cose run, calls nothing and reports no error in the next 400 ms', async () => {
    const onLayoutStop = vi.fn()
    const view = render(<TypedNetwork graph={network('network-tf1')} onLayoutStop={onLayoutStop} />)
    const cy = only()
    expect(runs(cy)).toBe(1)
    view.unmount()
    expect(cy.destroyed()).toBe(true)
    // The stopped run's last frame comes one frame later; had it thrown, Vitest would fail the run.
    await pause(400)
    expect(onLayoutStop).not.toHaveBeenCalled()
    expect(vi.mocked(console.error)).not.toHaveBeenCalled()
  })

  // Outside act, React destroys the core in the commit and runs the effects' cleanup in a later
  // task, and the slow commit lets a frame of the run fall between: cose's frames refresh the
  // positions from 250 ms into a run. No error listener, so a late error fails the run.
  it.each<[string, string | undefined]>([
    ['cose', undefined],
    ['cola', 'cola'],
  ])('dropped by a plain update 320 ms into a %s run, calls nothing and throws nothing afterwards', async (_name, layout) => {
    const onLayoutStop = vi.fn()
    let drop = () => {}
    render(<DroppingPage layout={layout} onLayoutStop={onLayoutStop} onReady={(next) => { drop = next }} />)
    const cy = only()
    await pause(320)
    expect(onLayoutStop).not.toHaveBeenCalled()
    const environment = globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }
    const actEnvironment = environment.IS_REACT_ACT_ENVIRONMENT
    environment.IS_REACT_ACT_ENVIRONMENT = false
    try {
      setTimeout(() => drop(), 0)
      await new Promise<void>((resolve) => setTimeout(resolve, 450))
    } finally {
      environment.IS_REACT_ACT_ENVIRONMENT = actEnvironment
    }
    expect(cy.destroyed()).toBe(true)
    expect(onLayoutStop).not.toHaveBeenCalled()
  })

  it("under the reduced-motion stub, a switch from cose to grid mid-run leaves grid's positions in place", async () => {
    reduceMotion()
    const onLayoutStop = vi.fn()
    const graph = network('network-all-edges')
    const view = render(<TypedNetwork graph={graph} onLayoutStop={onLayoutStop} />)
    const cy = only()
    await pause(50)
    expect(onLayoutStop).not.toHaveBeenCalled()
    view.rerender(<TypedNetwork graph={graph} layout="grid" onLayoutStop={onLayoutStop} />)
    expect(runs(cy)).toBe(2)
    // grid without animation places every node inside run().
    expect(onLayoutStop).toHaveBeenCalledTimes(1)
    const atGrid = positions(cy)
    await pause(400)
    expect(moved(atGrid, positions(cy))).toBe(0)
    expect(onLayoutStop).toHaveBeenCalledTimes(1)
  })

  it('stopQuietly on a plain headless core, beside plain stop() as the control', async () => {
    const elements = buildElements(network('network-all-edges'), { showNonSupporting: true }).elements
    const plainCore = () => cytoscape({ headless: true, styleEnabled: true, elements, style: TYPED_STYLESHEET })
    const wait = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms))
    const errors: string[] = []
    const onError = (event: ErrorEvent) => {
      errors.push(String(event.error?.message))
      event.preventDefault()
    }

    // grid without animation places every node inside run(), so a node that moves afterwards
    // was moved by the cose run stopped before it.
    const switchToGrid = async (quietly: boolean) => {
      const cy = plainCore()
      const run = cy.layout(layoutOptions('cose', { reducedMotion: false }))
      run.run()
      await wait(50)
      if (quietly) stopQuietly(cy, run)
      else run.stop()
      cy.layout({ name: 'grid', animate: false }).run()
      const atGrid = positions(cy)
      await wait(400)
      const count = moved(atGrid, positions(cy))
      cy.destroy()
      return { moved: count, of: atGrid.length, errors: errors.splice(0) }
    }
    // The order react-cytoscapejs gives: the core is destroyed before the effect's cleanup stops the run.
    const destroyThenStop = async (quietly: boolean) => {
      const cy = plainCore()
      const run = cy.layout(layoutOptions('cose', { reducedMotion: false }))
      run.run()
      cy.destroy()
      if (quietly) stopQuietly(cy, run)
      else run.stop()
      await wait(400)
      return errors.splice(0)
    }

    // Only this row listens: while a listener is attached, a late error no longer fails the run.
    window.addEventListener('error', onError)
    try {
      const quietSwitch = await switchToGrid(true)
      const quietDestroy = await destroyThenStop(true)
      const plainSwitch = await switchToGrid(false)
      const plainDestroy = await destroyThenStop(false)

      // One comparison, so that a failure shows every case.
      const cases = { quietSwitch, quietDestroy, plainSwitch: { ...plainSwitch, moved: plainSwitch.moved > 0 }, plainDestroy }
      expect(cases).toEqual({
        quietSwitch: { moved: 0, of: 12, errors: [] },
        quietDestroy: [],
        plainSwitch: { moved: true, of: 12, errors: [] },
        plainDestroy: ["Cannot read properties of null (reading 'notify')"],
      })
    } finally {
      window.removeEventListener('error', onError)
    }
  })
})

describe('reduced motion', () => {
  it('gives cose an infinite animationThreshold and grid no animation under the stub, and neither without it', () => {
    const graph = network('network-tf1')
    const calls = recordLayouts()
    const plain = render(<TypedNetwork graph={graph} />)
    plain.rerender(<TypedNetwork graph={graph} layout="grid" />)
    plain.unmount()
    reduceMotion()
    const reduced = render(<TypedNetwork graph={graph} />)
    reduced.rerender(<TypedNetwork graph={graph} layout="grid" />)
    const options = calls.map((call) => call.options)
    expect(options).toMatchObject([
      { name: 'cose', animate: true },
      { name: 'grid', animate: true },
      { name: 'cose', animate: true, animationThreshold: Number.POSITIVE_INFINITY },
      { name: 'grid', animate: false },
    ])
    expect(options[0]).not.toHaveProperty('animationThreshold')
  })

  it('under the stub, moves no node until the cose run ends, and then at least one', async () => {
    reduceMotion()
    const onLayoutStop = vi.fn()
    render(<TypedNetwork graph={network('network-tf1')} onLayoutStop={onLayoutStop} />)
    const cy = only()
    const atMount = positions(cy)
    await pause(100)
    expect(onLayoutStop).not.toHaveBeenCalled()
    expect(positions(cy)).toEqual(atMount)
    // A cose run takes about 35 frames here; every read before its end finds the nodes where they were.
    const reads = await positionsUntilStop(cy, onLayoutStop)
    expect(reads.filter((read) => moved(atMount, read) > 0)).toEqual([])
    expect(moved(atMount, positions(cy))).toBeGreaterThan(0)
  })

  it('without the stub, the same run moves nodes before it ends', async () => {
    const onLayoutStop = vi.fn()
    render(<TypedNetwork graph={network('network-tf1')} onLayoutStop={onLayoutStop} />)
    const cy = only()
    const atMount = positions(cy)
    const reads = await positionsUntilStop(cy, onLayoutStop)
    expect(reads.some((read) => moved(atMount, read) > 0)).toBe(true)
  })
})

describe('the default view and hiding', () => {
  it('draws no tested-absent edge for the default request, draws it for outcome=all, and hides it without a new run', () => {
    const view = render(<TypedNetwork graph={network('network-tf1')} />)
    const cy = only()
    expect(cy.$id('relationship:7').length).toBe(0)

    const outcomeAll = network('network-tf1-outcome-all')
    view.rerender(<TypedNetwork graph={outcomeAll} />)
    const edge = cy.$id('relationship:7')
    // entity:2 is reached only through relationship:7.
    const reached = cy.$id('entity:2')
    expect(edge.hasClass('ln-tested-absent')).toBe(true)
    expect(edge.style('display')).toBe('element')
    expect(reached.style('display')).toBe('element')
    expect(view.getByRole('img').getAttribute('aria-label')).toBe('Network of 4 entities and 4 relationships.')
    const before = runs(cy)

    view.rerender(<TypedNetwork graph={outcomeAll} showNonSupporting={false} />)
    expect(edge.hasClass('hidden')).toBe(true)
    expect(edge.style('display')).toBe('none')
    expect(reached.hasClass('hidden')).toBe(true)
    expect(reached.style('display')).toBe('none')
    expect(cy.$id('entity:7').style('display')).toBe('element')
    // The image's name counts what is drawn.
    expect(view.getByRole('img').getAttribute('aria-label')).toBe('Network of 3 entities and 3 relationships.')
    expect(runs(cy)).toBe(before)
  })
})

describe('taps on string ids', () => {
  it('resolve string ids to their records, the core to null, and ignore a hidden element', () => {
    const graph = network('network-tf1-outcome-all')
    const onSelect = vi.fn<(selection: TypedSelectionEvent | null) => void>()
    render(<TypedNetwork graph={graph} onSelect={onSelect} showNonSupporting={false} />)
    const cy = only()

    act(() => {
      cy.$id('relationship:5').emit('tap')
    })
    expect(onSelect).toHaveBeenLastCalledWith({ kind: 'edge', id: 'relationship:5', record: edgeOf(graph, 'relationship:5') })
    expect(onSelect.mock.lastCall?.[0]?.record).toBe(edgeOf(graph, 'relationship:5'))
    act(() => {
      cy.$id('entity:7').emit('tap')
    })
    expect(onSelect).toHaveBeenLastCalledWith({ kind: 'node', id: 'entity:7', record: nodeOf(graph, 'entity:7') })
    act(() => {
      cy.emit('tap')
    })
    expect(onSelect).toHaveBeenLastCalledWith(null)

    const before = onSelect.mock.calls.length
    act(() => {
      cy.$id('relationship:7').emit('tap')
    })
    act(() => {
      cy.$id('entity:2').emit('tap')
    })
    expect(onSelect).toHaveBeenCalledTimes(before)
  })

  it('resolve a ref of another kind, used verbatim', () => {
    const graph = graphOfNetwork(makeNetworkResponse({ nodes: ppiNodes, edges: [ppiEdge] }))
    const onSelect = vi.fn<(selection: TypedSelectionEvent | null) => void>()
    render(<TypedNetwork graph={graph} onSelect={onSelect} />)
    act(() => {
      only().$id('ppi-interaction:42').emit('tap')
    })
    expect(onSelect).toHaveBeenLastCalledWith({ kind: 'edge', id: 'ppi-interaction:42', record: ppiEdge })
  })
})

describe('selection', () => {
  it('survives a data change, and nothing is drawn as selected once the selected edge is hidden', () => {
    const selected: TypedSelection = { kind: 'edge', id: 'relationship:6' }
    const view = render(<TypedNetwork graph={network('network-tf1-v1')} selected={selected} />)
    const cy = only()
    expect(cy.$id('relationship:6').hasClass('is-selected')).toBe(true)
    view.rerender(<TypedNetwork graph={network('network-tf1')} selected={selected} />)
    expect(only()).toBe(cy)
    expect(cy.$('.is-selected').map((element) => element.id())).toEqual(['relationship:6'])

    const drawnSelected = () =>
      cy.$('.is-selected').filter((element) => element.style('display') !== 'none').map((element) => element.id())
    const outcomeAll = network('network-tf1-outcome-all')
    const testedAbsent: TypedSelection = { kind: 'edge', id: 'relationship:7' }
    view.rerender(<TypedNetwork graph={outcomeAll} selected={testedAbsent} />)
    expect(drawnSelected()).toEqual(['relationship:7'])
    view.rerender(<TypedNetwork graph={outcomeAll} selected={testedAbsent} showNonSupporting={false} />)
    expect(drawnSelected()).toEqual([])
    // Still selected in the prop, which is the page's to clear.
    expect(cy.$id('relationship:7').hasClass('is-selected')).toBe(true)
  })
})

describe('instances', () => {
  it('make no layout call on a destroyed instance through a null-and-back sequence', () => {
    const calls = recordLayouts()
    const view = render(<TypedNetwork graph={network('network-tf1')} />)
    view.rerender(<TypedNetwork graph={network('network-tf1-visitor')} />)
    view.rerender(<TypedNetwork graph={null} />)
    view.rerender(<TypedNetwork graph={network('network-tf1')} />)
    expect(calls.map((call) => [instances().indexOf(call.cy), call.options.name, call.destroyed])).toEqual([
      [0, 'cose', false],
      [1, 'cose', false],
    ])
    expect(instances()[0].destroyed()).toBe(true)
    expect(instances().map(runs)).toEqual([1, 1])
  })

  it('keep two networks on one page apart', () => {
    const left = vi.fn<(selection: TypedSelectionEvent | null) => void>()
    const right = vi.fn<(selection: TypedSelectionEvent | null) => void>()
    const tf1 = network('network-tf1')
    const allEdges = network('network-all-edges')
    const page = (selected: TypedSelection | null) => (
      <>
        <TypedNetwork graph={tf1} onSelect={left} selected={selected} />
        <TypedNetwork graph={allEdges} onSelect={right} />
      </>
    )
    const view = render(page(null))
    expect(instances()).toHaveLength(2)
    const [a, b] = instances()
    expect([a.nodes().length, b.nodes().length]).toEqual([tf1.nodes.length, allEdges.nodes.length])

    act(() => {
      a.$id('entity:7').emit('tap')
    })
    expect(left).toHaveBeenCalledTimes(1)
    expect(right).not.toHaveBeenCalled()
    act(() => {
      b.$id('entity:7').emit('tap')
    })
    expect(left).toHaveBeenCalledTimes(1)
    expect(right).toHaveBeenCalledTimes(1)

    view.rerender(page({ kind: 'node', id: 'entity:7' }))
    expect(a.$id('entity:7').hasClass('is-selected')).toBe(true)
    expect(b.$id('entity:7').hasClass('is-selected')).toBe(false)
    expect([runs(a), runs(b)]).toEqual([1, 1])
  })
})

describe('the status line and the image', () => {
  it('keeps one status line through every state, outside the counted image, with no class anywhere', () => {
    const tf1 = network('network-tf1')
    const view = render(<TypedNetwork graph={null} state="loading" />)
    const status = view.getByRole('status')
    expect(status.textContent).toBe('Loading the network…')
    view.rerender(<TypedNetwork graph={tf1} state="ready" />)
    expect(view.getByRole('status')).toBe(status)
    expect(status.textContent).toBe('')
    view.rerender(<TypedNetwork graph={tf1} state="updating" />)
    expect(view.getByRole('status')).toBe(status)
    expect(status.textContent).toBe('Updating the network…')
    view.rerender(<TypedNetwork graph={tf1} state="ready" />)
    expect(view.getByRole('status')).toBe(status)
    expect(status.textContent).toBe('')

    view.rerender(<TypedNetwork graph={null} state="error" error={json503()} />)
    expect(view.getByRole('status')).toBe(status)
    expect(status.textContent).toBe('This network is too large to compute. Narrow the seeds, layers or filters.')
    expect(view.queryByRole('img')).toBeNull()

    view.rerender(<TypedNetwork graph={tf1} state="ready" />)
    const image = view.getByRole('img')
    expect(image.getAttribute('aria-label')).toBe('Network of 3 entities and 3 relationships.')
    expect(image.contains(status)).toBe(false)
    expect(view.container.querySelector('[class]')).toBeNull()
  })
})
