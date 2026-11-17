import { cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { hostile, hostileNetwork, makeNetworkResponse, ppiEdge, ppiNodes, presentationAllKeys } from '../../../mocks/fixtures/typedGraph/extras'
import { recorded, recordRequests } from '../../../test/graphHooks'
import type { RequestLog } from '../../../test/graphHooks'
import type { GraphEdge, GraphNetworkResponse, GraphVocabularies } from '../types'
import { graphOfNetwork } from '../network/graph'
import type { TypedGraph, TypedGraphNode, TypedSelectionEvent } from '../network/graph'
import { TypedEdgesTable } from './TypedEdgesTable'

let log: RequestLog

beforeEach(() => {
  log = recordRequests()
  vi.spyOn(console, 'warn')
  vi.spyOn(console, 'error')
})

afterEach(() => {
  // Unmounted first, so that what the unmount reports is checked too.
  cleanup()
  log.stop()
  const calls = [...vi.mocked(console.warn).mock.calls, ...vi.mocked(console.error).mock.calls]
  vi.restoreAllMocks()
  // The table only hands a selection to the page: it never sends a request of its own.
  expect(log.urls).toEqual([])
  expect(log.unhandled).toEqual([])
  expect(calls.map((args) => args.map(String).join(' ').slice(0, 300))).toEqual([])
})

// Words copied from the spec's sections 8.3, 8.10 and 13.2, never from the module under test.
const CAPTION = 'Relationships in view'
const EMPTY = 'No relationship in view.'
const ENTITIES = 'Entities in view'
const SORT_LABELS = [
  'First entity', 'Second entity', 'Type', 'Independent evidence', 'Supporting results (assertions)', 'Reporting publications',
]
const SHARED = "Every entity in view is provisional: created from a release's evidence; no reference release has stated this identifier yet."
const REL5 = 'Transcriptional regulation: SYN-G1 (Gene) to SYN-TF1 (Gene)'
const REL6 = 'Transcriptional regulation: SYN-TF1 (Gene) to SYN-G1 (Gene)'
const REL8 = 'Transcriptional regulation: SYN-TF1 (Gene) to SYN-G4 (Gene)'

const vocabularies = () => recorded<GraphVocabularies>('vocabularies').body
const network = (name: string): TypedGraph => graphOfNetwork(recorded<GraphNetworkResponse>(name).body)

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

function tableBody(caption = CAPTION): HTMLElement {
  return within(screen.getByRole('table', { name: caption })).getAllByRole('rowgroup')[1]
}

/** The row buttons, in the order shown. */
function rowNames(): string[] {
  return within(tableBody()).getAllByRole('button').map((button) => button.textContent ?? '')
}

/** The cells after the row's header, for the row whose button has this name. */
function cellsOf(name: string): string[] {
  const row = screen.getByRole('button', { name }).closest('tr')
  if (row === null) throw new Error(`no row for ${name}`)
  return within(row).getAllByRole('cell').map((cell) => cell.textContent ?? '')
}

function listedNames(): string[] {
  return within(screen.getByRole('list', { name: ENTITIES })).getAllByRole('button').map((button) => button.textContent ?? '')
}

function sortedHeaders(): HTMLElement[] {
  return screen.getAllByRole('columnheader').filter((header) => header.hasAttribute('aria-sort'))
}

describe('TypedEdgesTable: keyboard', () => {
  it('TT-1: Tab reaches the six sort buttons, the row buttons in rank order, then every entity in view', async () => {
    const user = userEvent.setup()
    render(<TypedEdgesTable graph={network('network-tf1')} vocabularies={vocabularies()} />)
    const caption = screen.getByRole('table', { name: CAPTION }).querySelector('caption')
    const label = screen.getByText(ENTITIES)
    const reached: Element[] = []
    for (let i = 0; i < 13; i += 1) {
      await user.tab()
      reached.push(document.activeElement as Element)
    }
    expect(reached.slice(0, 12).map((element) => element.textContent)).toEqual([
      ...SORT_LABELS, REL5, REL6, REL8, 'SYN-G1 (Gene)', 'SYN-G4 (Gene)', 'SYN-TF1 (Gene), seed',
    ])
    expect(reached.slice(0, 12).map((element) => element.tagName)).toEqual(new Array<string>(12).fill('BUTTON'))
    // The thirteenth Tab leaves the table: the caption and the list's label are focus targets only.
    expect(reached[12]).toBe(document.body)
    expect(caption).not.toBeNull()
    expect(reached).not.toContain(caption)
    expect(reached).not.toContain(label)
  }, 20_000)

  it('TT-2: Enter and Space sort by the second entity, aria-sort on that header only; a third press gives rank order', async () => {
    const user = userEvent.setup()
    const loaded = network('network-tf1')
    // Frozen, so a sort made in place would throw.
    const graph: TypedGraph = { ...loaded, nodes: Object.freeze([...loaded.nodes]), edges: Object.freeze([...loaded.edges]) }
    render(<TypedEdgesTable graph={graph} vocabularies={vocabularies()} />)
    const button = screen.getByRole('button', { name: 'Second entity' })
    const header = button.closest('th')
    expect(rowNames()).toEqual([REL5, REL6, REL8])
    expect(sortedHeaders()).toEqual([])
    button.focus()
    await user.keyboard('{Enter}')
    expect(header).toHaveAttribute('aria-sort', 'ascending')
    expect(sortedHeaders()).toEqual([header])
    expect(rowNames()).toEqual([REL6, REL8, REL5])
    // The glyph is shown and left out of the name.
    expect(button.textContent).toBe('Second entity ▲')
    expect(screen.getByRole('button', { name: 'Second entity' })).toBe(button)
    await user.keyboard(' ')
    expect(header).toHaveAttribute('aria-sort', 'descending')
    expect(sortedHeaders()).toEqual([header])
    expect(rowNames()).toEqual([REL5, REL8, REL6])
    expect(button.textContent).toBe('Second entity ▼')
    await user.keyboard('{Enter}')
    expect(sortedHeaders()).toEqual([])
    expect(rowNames()).toEqual([REL5, REL6, REL8])
    expect(button.textContent).toBe('Second entity')
    expect(document.activeElement).toBe(button)
    expect(graph.edges.map((edge) => edge.id)).toEqual(['relationship:5', 'relationship:6', 'relationship:8'])
    expect(graph.nodes.map((node) => node.id)).toEqual(['entity:1', 'entity:4', 'entity:7'])
  }, 20_000)

  it("TT-3: Enter and Space on a row button each call onSelect with the graph's edge; focus alone calls nothing", async () => {
    const user = userEvent.setup()
    const onSelect = vi.fn<(selection: TypedSelectionEvent) => void>()
    const graph = network('network-tf1')
    const record = edgeOf(graph, 'relationship:6')
    render(<TypedEdgesTable graph={graph} vocabularies={vocabularies()} onSelect={onSelect} />)
    const button = screen.getByRole('button', { name: REL6 })
    for (let i = 0; i < 8 && document.activeElement !== button; i += 1) await user.tab()
    expect(document.activeElement).toBe(button)
    expect(onSelect).not.toHaveBeenCalled()
    await user.keyboard('{Enter}')
    expect(onSelect.mock.calls).toEqual([[{ kind: 'edge', id: 'relationship:6', record }]])
    await user.keyboard(' ')
    expect(onSelect.mock.calls).toEqual([[{ kind: 'edge', id: 'relationship:6', record }], [{ kind: 'edge', id: 'relationship:6', record }]])
    expect(onSelect.mock.calls.map(([selection]) => selection.record)).toEqual([record, record])
    expect(onSelect.mock.calls[0][0].record).toBe(record)
  }, 20_000)

  it('TT-4: the selected row is marked by aria-current, an aria-hidden glyph and the weight, its name unchanged', () => {
    const graph = network('network-tf1')
    const view = render(<TypedEdgesTable graph={graph} vocabularies={vocabularies()} selected={{ kind: 'edge', id: 'relationship:6' }} />)
    const button = screen.getByRole('button', { name: REL6 })
    expect(button).toHaveAttribute('aria-current', 'true')
    const glyph = button.querySelector('[aria-hidden="true"]')
    expect(glyph?.textContent).toBe('▶ ')
    expect(button.textContent).toBe(`▶ ${REL6}`)
    expect(button.style.fontWeight).toBe('700')
    expect(screen.getAllByRole('button').filter((other) => other.hasAttribute('aria-current'))).toEqual([button])
    expect(screen.getByRole('button', { name: REL5 }).style.fontWeight).toBe('400')
    // An entity selection marks its entry in the list instead.
    view.rerender(<TypedEdgesTable graph={graph} vocabularies={vocabularies()} selected={{ kind: 'node', id: 'entity:7' }} />)
    const entity = screen.getByRole('button', { name: 'SYN-TF1 (Gene), seed' })
    expect(entity).toHaveAttribute('aria-current', 'true')
    expect(entity.textContent).toBe('▶ SYN-TF1 (Gene), seed')
    expect(screen.getAllByRole('button').filter((other) => other.hasAttribute('aria-current'))).toEqual([entity])
  })
})

describe('TypedEdgesTable: words', () => {
  it('TT-5: one shared status sentence under the table, else a status word beside each name; none for a null status', () => {
    const graph = network('network-tf1')
    const first = render(<TypedEdgesTable graph={graph} vocabularies={vocabularies()} />)
    expect(screen.getAllByText(SHARED)).toHaveLength(1)
    expect(screen.getByRole('table', { name: CAPTION }).nextElementSibling?.textContent).toBe(SHARED)
    expect(cellsOf(REL5).slice(0, 2)).toEqual(['SYN-G1 (Gene)', 'SYN-TF1 (Gene)'])
    expect(listedNames()).toEqual(['SYN-G1 (Gene)', 'SYN-G4 (Gene)', 'SYN-TF1 (Gene), seed'])
    for (const cell of screen.getAllByRole('cell')) expect(cell.textContent).not.toContain(', provisional')
    for (const button of screen.getAllByRole('button')) expect(button.textContent).not.toContain(', provisional')
    first.unmount()

    const mixed: TypedGraph = {
      ...graph, nodes: graph.nodes.map((node) => (node.id === 'entity:1' ? { ...node, status: 'merged' } : node)),
    }
    const second = render(<TypedEdgesTable graph={mixed} vocabularies={vocabularies()} />)
    expect(second.container.textContent).not.toContain('Every entity in view')
    expect(cellsOf(REL5).slice(0, 2)).toEqual(['SYN-G1 (Gene), merged', 'SYN-TF1 (Gene), provisional'])
    expect(listedNames()).toEqual(['SYN-G1 (Gene), merged', 'SYN-G4 (Gene), provisional', 'SYN-TF1 (Gene), provisional, seed'])
    // The row's name is the panel heading's, which carries no status.
    expect(screen.getByRole('button', { name: REL5 })).toBeInTheDocument()
    second.unmount()

    const ppi = graphOfNetwork(makeNetworkResponse({ nodes: ppiNodes, edges: [ppiEdge] }))
    const third = render(<TypedEdgesTable graph={ppi} vocabularies={vocabularies()} />)
    expect(third.container.textContent).not.toContain('Every entity in view')
    const rows = within(tableBody()).getAllByRole('row')
    expect(rows).toHaveLength(1)
    expect(within(rows[0]).getAllByRole('cell').slice(0, 2).map((cell) => cell.textContent)).toEqual([
      'SYN-PPI-1 (Protein (UniProtKB entry))', 'SYN-PPI-2 (Protein (UniProtKB entry))',
    ])
    expect(listedNames()).toEqual(['SYN-PPI-1 (Protein (UniProtKB entry)), seed', 'SYN-PPI-2 (Protein (UniProtKB entry))'])
  })

  it('TT-6: node_label_namespace names relationship:11 by its primary identifier', () => {
    const view = render(<TypedEdgesTable graph={network('network-all-edges')} vocabularies={vocabularies()} presentation={presentationAllKeys} />)
    const name = 'Transcriptional regulation: SYN-TF3 (Gene) to SYN-G3 (Gene)'
    expect(cellsOf(name).slice(0, 2)).toEqual(['SYN-TF3 (Gene)', 'SYN-G3 (Gene)'])
    expect(listedNames()).toContain('SYN-G3 (Gene)')
    expect(view.container.textContent).toContain('SYN-G3')
    expect(view.container.textContent).not.toContain('SYN-ALPHA')
  })

  it('TT-7: with no relationship in view, the empty row, the shared sentence and the seed as a button that opens its panel', async () => {
    const user = userEvent.setup()
    const onSelect = vi.fn<(selection: TypedSelectionEvent) => void>()
    const graph = network('network-p2-inconclusive')
    render(<TypedEdgesTable graph={graph} vocabularies={vocabularies()} onSelect={onSelect} />)
    const cells = within(tableBody()).getAllByRole('cell')
    expect(cells.map((cell) => cell.textContent)).toEqual([EMPTY])
    expect(cells[0]).toHaveAttribute('colspan', '12')
    expect(screen.getAllByText(SHARED)).toHaveLength(1)
    const list = screen.getByRole('list', { name: ENTITIES })
    const button = within(list).getByRole('button', { name: 'SYN-P2 (Protein (UniProtKB entry)), seed' })
    expect(button.textContent).not.toContain(', provisional')
    expect(within(list).getAllByRole('button')).toEqual([button])
    for (let i = 0; i < 7 && document.activeElement !== button; i += 1) await user.tab()
    expect(document.activeElement).toBe(button)
    expect(onSelect).not.toHaveBeenCalled()
    await user.keyboard('{Enter}')
    expect(onSelect.mock.calls).toEqual([[{ kind: 'node', id: 'entity:12', record: nodeOf(graph, 'entity:12') }]])
  }, 20_000)

  it('TT-8: with no graph, the caption and the empty row, and no entity list or status sentence', () => {
    const view = render(<TypedEdgesTable graph={null} vocabularies={vocabularies()} />)
    expect(screen.getByRole('table', { name: CAPTION })).toBeInTheDocument()
    expect(within(tableBody()).getAllByRole('cell').map((cell) => cell.textContent)).toEqual([EMPTY])
    expect(screen.queryByRole('list')).toBeNull()
    expect(screen.queryByText(ENTITIES)).toBeNull()
    expect(view.container.querySelectorAll('p')).toHaveLength(0)
    expect(view.container.textContent).not.toContain('Every entity in view')
  })
})

describe('TypedEdgesTable: two on a page, style and source text', () => {
  it('TT-9: two tables on one page: distinct captions and label ids, each list named by its own label, Tab in document order', async () => {
    const user = userEvent.setup()
    const graph = network('network-p2-inconclusive')
    render(
      <>
        <TypedEdgesTable graph={graph} vocabularies={vocabularies()} />
        <TypedEdgesTable graph={graph} vocabularies={vocabularies()} caption="Second view" />
      </>,
    )
    const tables = [screen.getByRole('table', { name: CAPTION }), screen.getByRole('table', { name: 'Second view' })]
    const lists = screen.getAllByRole('list', { name: ENTITIES })
    expect(lists).toHaveLength(2)
    const labelIds = lists.map((list) => list.getAttribute('aria-labelledby'))
    expect(new Set(labelIds).size).toBe(2)
    tables.forEach((table, index) => {
      const outer = table.parentElement as HTMLElement
      const label = outer.querySelector('p[id]')
      expect(label?.textContent).toBe(ENTITIES)
      expect(outer.contains(lists[index])).toBe(true)
      expect(lists[index].getAttribute('aria-labelledby')).toBe(label?.id)
      expect(document.getElementById(labelIds[index] ?? '')).toBe(label)
    })
    const buttons = screen.getAllByRole('button')
    expect(buttons).toHaveLength(14)
    const reached: Element[] = []
    for (let i = 0; i < buttons.length; i += 1) {
      await user.tab()
      reached.push(document.activeElement as Element)
    }
    expect(reached).toEqual(buttons)
  }, 20_000)

  it('TT-10: no class, every list a list, pointer cursors, the sort buttons styled by the cursor alone, data-selectable', () => {
    const view = render(<TypedEdgesTable graph={network('network-tf1')} vocabularies={vocabularies()} selected={{ kind: 'edge', id: 'relationship:6' }} />)
    const container = view.container
    expect(container.querySelector('[class]')).toBeNull()
    expect(container.querySelectorAll('ul')).toHaveLength(1)
    expect(container.querySelectorAll('ul:not([role="list"])')).toHaveLength(0)
    const buttons = Array.from(container.querySelectorAll('button'))
    expect(buttons).toHaveLength(12)
    for (const button of buttons) expect(button.style.cursor).toBe('pointer')
    for (const label of SORT_LABELS) {
      const button = screen.getByRole('button', { name: label })
      expect(button.style.length).toBe(1)
    }
    for (const cell of Array.from(container.querySelectorAll<HTMLElement>('th, td'))) expect(cell.style.padding).toBe('4px 8px')
    for (const cell of within(tableBody()).getAllByRole('cell')) expect(cell.style.whiteSpace).toBe('pre-wrap')
    const outer = container.firstElementChild as HTMLElement
    expect(outer.tagName).toBe('DIV')
    expect(outer).toHaveAttribute('data-selectable')
    expect(outer.style.width).toBe('100%')
    expect(screen.getByRole('table', { name: CAPTION }).querySelector('caption')).toHaveAttribute('tabindex', '-1')
  })

  it('TT-11: the hostile network renders as literal text in the cells, the row button and the entity buttons', () => {
    const view = render(<TypedEdgesTable graph={graphOfNetwork(hostileNetwork)} vocabularies={vocabularies()} />)
    expect(view.container.querySelector('[data-hostile]')).toBeNull()
    const first = `SYN-${hostile('nodes[0].label')} (Gene)`
    const second = `SYN-${hostile('nodes[1].label')} (Gene)`
    const name = `Transcriptional regulation: ${first} to ${second}`
    expect(rowNames()).toEqual([name])
    const cells = cellsOf(name)
    expect(cells.slice(0, 2)).toEqual([first, second])
    expect(cells[cells.length - 1]).toContain(hostile('nodes[0].label'))
    expect(cells[cells.length - 1]).toContain(hostile('nodes[1].label'))
    expect(listedNames()).toEqual([`${first}, seed`, second])
    expect(view.container.querySelector('[data-hostile]')).toBeNull()
  })
})
