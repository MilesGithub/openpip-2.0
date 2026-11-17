import { useId, useMemo, useState } from 'react'
import type { CSSProperties } from 'react'
import type { GraphPresentation, GraphVocabularies } from '../types'
import type { TypedGraph, TypedSelection, TypedSelectionEvent } from '../network/graph'
import { defaultShowNonSupporting } from '../network/visibility'
import { tableStatusSentence } from '../panels/panelWords'
import { CELL_STYLE, LINK_BUTTON_STYLE, SOURCE_TEXT_STYLE } from '../panels/text'
import { edgeRows, entityRows, nextSort, sortEdgeRows } from './edgeRows'
import type { EdgeSort, EdgeSortKey, EntityRow } from './edgeRows'
import { TABLE_TEXT } from './tableWords'

export interface TypedEdgesTableProps {
  graph: TypedGraph | null                    // the same object TypedNetwork gets
  vocabularies?: GraphVocabularies | null
  presentation?: GraphPresentation | null     // node_label_namespace (B1-2)
  showNonSupporting?: boolean                 // the canvas's value; default defaultShowNonSupporting(graph)
  selected?: TypedSelection | null            // marked on its row, or on its entity in the list
  onSelect?: (selection: TypedSelectionEvent) => void
  caption?: string                            // default TABLE_TEXT.caption; two tables on one page take distinct captions
  defaultSort?: EdgeSort | null               // default null: the API's rank order
}

const TEXT_CELL_STYLE: CSSProperties = { ...CELL_STYLE, ...SOURCE_TEXT_STYLE }

interface SortHeaderProps {
  label: string
  sortKey: EdgeSortKey
  sort: EdgeSort | null
  setSort: (sort: EdgeSort | null) => void
}

/** The button keeps the header's look, which it inherits under Tailwind's reset; the glyph stays out of its name. */
function SortHeader({ label, sortKey, sort, setSort }: SortHeaderProps) {
  const sorted = sort !== null && sort.key === sortKey
  return (
    <th scope="col" aria-sort={sorted ? sort.direction : undefined} style={CELL_STYLE}>
      <button type="button" onClick={() => setSort(nextSort(sort, sortKey))} style={{ cursor: 'pointer' }}>
        {label}
        {sorted && <span aria-hidden="true">{sort.direction === 'ascending' ? TABLE_TEXT.ascending : TABLE_TEXT.descending}</span>}
      </button>
    </th>
  )
}

interface EntityListProps {
  entities: readonly EntityRow[]
  selected?: TypedSelection | null
  onSelect?: (selection: TypedSelectionEvent) => void
}

/**
 * Every entity in view, so the keyboard opens any entity panel as a tap on its node does, with one
 * request that spends none of the network's allowance. The label takes focus when a panel opened
 * from the list closes after its button has gone, as the caption does for a row.
 */
function EntityList({ entities, selected, onSelect }: EntityListProps) {
  const listId = useId()
  return (
    <>
      <p id={listId} tabIndex={-1} style={{ fontWeight: 600, margin: '0 0 4px' }}>{TABLE_TEXT.entities}</p>
      <ul role="list" aria-labelledby={listId} style={{ listStyle: 'none', margin: 0, padding: 0 }}>
        {entities.map((row) => {
          const isSelected = selected?.kind === 'node' && selected.id === row.id
          return (
            <li key={row.id}>
              <button
                type="button"
                aria-current={isSelected ? 'true' : undefined}
                onClick={() => onSelect?.({ kind: 'node', id: row.id, record: row.node })}
                style={{ ...LINK_BUTTON_STYLE, ...SOURCE_TEXT_STYLE, fontWeight: isSelected ? 700 : 400 }}
              >
                {isSelected && <span aria-hidden="true">{TABLE_TEXT.selected}</span>}
                {row.name}
              </button>
            </li>
          )
        })}
      </ul>
    </>
  )
}

/**
 * The canvas's keyboard equivalent. Every control is a native button, since a focusable row gets no
 * click from Enter or Space, and a panel opens on activation only, never on focus: the relationship
 * page spends the allowance the network shares. The selected row is marked by a glyph and a weight,
 * never by the bar's colour alone.
 */
export function TypedEdgesTable(props: TypedEdgesTableProps) {
  const {
    graph, vocabularies, presentation, showNonSupporting, selected, onSelect, caption = TABLE_TEXT.caption, defaultSort,
  } = props
  const show = showNonSupporting ?? defaultShowNonSupporting(graph)
  const rows = useMemo(
    () => edgeRows(graph, { showNonSupporting: show, presentation, vocabularies }),
    [graph, show, presentation, vocabularies],
  )
  const entities = useMemo(
    () => entityRows(graph, { showNonSupporting: show, presentation, vocabularies }),
    [graph, show, presentation, vocabularies],
  )
  // One sentence rather than the same status word beside every name.
  const status = useMemo(() => tableStatusSentence(entities.map((row) => row.node.status)), [entities])
  const [sort, setSort] = useState<EdgeSort | null>(defaultSort ?? null)
  const shown = useMemo(() => sortEdgeRows(rows, sort), [rows, sort])
  return (
    <div data-selectable style={{ width: '100%' }}>
      <table style={{ width: '100%', borderCollapse: 'collapse' }}>
        <caption tabIndex={-1} style={{ textAlign: 'left', fontWeight: 600 }}>{caption}</caption>
        <thead>
          <tr>
            <th scope="col" style={CELL_STYLE}>{TABLE_TEXT.relationship}</th>
            <SortHeader label={TABLE_TEXT.first} sortKey="first" sort={sort} setSort={setSort} />
            <SortHeader label={TABLE_TEXT.second} sortKey="second" sort={sort} setSort={setSort} />
            <SortHeader label={TABLE_TEXT.type} sortKey="type" sort={sort} setSort={setSort} />
            <th scope="col" style={CELL_STYLE}>{TABLE_TEXT.direction}</th>
            <th scope="col" style={CELL_STYLE}>{TABLE_TEXT.effect}</th>
            <th scope="col" style={CELL_STYLE}>{TABLE_TEXT.level}</th>
            <th scope="col" style={CELL_STYLE}>{TABLE_TEXT.outcome}</th>
            <SortHeader label={TABLE_TEXT.evidence} sortKey="evidence" sort={sort} setSort={setSort} />
            <SortHeader label={TABLE_TEXT.assertions} sortKey="assertions" sort={sort} setSort={setSort} />
            <SortHeader label={TABLE_TEXT.publications} sortKey="publications" sort={sort} setSort={setSort} />
            <th scope="col" style={CELL_STYLE}>{TABLE_TEXT.description}</th>
          </tr>
        </thead>
        <tbody>
          {shown.length === 0 && (
            <tr>
              <td colSpan={12} style={CELL_STYLE}>{TABLE_TEXT.empty}</td>
            </tr>
          )}
          {shown.map((row) => {
            const isSelected = selected?.kind === 'edge' && selected.id === row.id
            return (
              <tr key={row.id}>
                <th scope="row" style={isSelected ? { ...CELL_STYLE, borderLeft: '3px solid var(--primary-deep)' } : CELL_STYLE}>
                  <button
                    type="button"
                    aria-current={isSelected ? 'true' : undefined}
                    onClick={() => onSelect?.({ kind: 'edge', id: row.id, record: row.edge })}
                    style={{ ...LINK_BUTTON_STYLE, ...SOURCE_TEXT_STYLE, fontWeight: isSelected ? 700 : 400 }}
                  >
                    {isSelected && <span aria-hidden="true">{TABLE_TEXT.selected}</span>}
                    {row.name}
                  </button>
                </th>
                <td style={TEXT_CELL_STYLE}>{row.first}</td>
                <td style={TEXT_CELL_STYLE}>{row.second}</td>
                <td style={TEXT_CELL_STYLE}>{row.type}</td>
                <td style={TEXT_CELL_STYLE}>{row.direction}</td>
                <td style={TEXT_CELL_STYLE}>{row.effect}</td>
                <td style={TEXT_CELL_STYLE}>{row.level}</td>
                <td style={TEXT_CELL_STYLE}>{row.outcome}</td>
                <td style={TEXT_CELL_STYLE}>{row.evidence}</td>
                <td style={TEXT_CELL_STYLE}>{row.assertions}</td>
                <td style={TEXT_CELL_STYLE}>{row.publications}</td>
                <td style={TEXT_CELL_STYLE}>{row.description}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
      {status !== null && <p style={{ margin: '4px 0' }}>{status}</p>}
      {entities.length > 0 && <EntityList entities={entities} selected={selected} onSelect={onSelect} />}
    </div>
  )
}
