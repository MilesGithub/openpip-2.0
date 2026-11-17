import { useState } from 'react'
import type { GraphSelection } from '../../../api/graph/params'
import { useGraphEntity } from '../../../api/graph/entities'
import { classifyGraphError } from '../../../api/graph/errors'
import type { GraphEntityRef, GraphPresentation, GraphVocabularies } from '../types'
import type { TypedGraphNode } from '../network/graph'
import { entityTypeLabel, slugWords, typeLabel } from '../network/words'
import { LEGEND_TEXT } from '../legend/legendText'
import { isEntityBody } from './guards'
import { PanelShell } from './PanelShell'
import { PanelStatus } from './PanelStatus'
import { panelParamsKey, selectionOf, usePanelDelay } from './panelParams'
import {
  ENTITY_TERMS, NO_REFERENCE_RELATIONSHIPS, NO_RELATIONSHIP_COUNTS, PANEL_LABELS, PANEL_LOADING, PANEL_WAITING,
  RELATIONSHIP_COUNTS_NOTE, endName, entityHeading, entityStatusSentence, mergedText, panelErrorFor, panelErrorText,
  panelMayRetry, relationshipName, uniqueNames,
} from './panelWords'
import { ReleaseList } from './ReleaseList'
import { isSyntheticShown, rowReleaseWords } from './releases'
import type { PanelSelection } from './selection'
import { CELL_STYLE, LINK_BUTTON_STYLE, LINK_STYLE, SUBHEADING_STYLE, arrayOf, clipText, isHttpUrl, subLevel } from './text'
import type { HeadingLevel } from './text'

export interface EntityPanelProps {
  entityRef: GraphEntityRef
  selection: GraphSelection | null             // selectionOf(the held query), a panel's params' selection, or X1's URL selection; null: no query seen yet (12.3; M1)
  node?: TypedGraphNode | null                 // the record the heading shows; X1's additive node keys arrive through it
  vocabularies?: GraphVocabularies | null
  presentation?: GraphPresentation | null
  onSelect?: (selection: PanelSelection | null) => void   // a reference relationship opens its panel (U33)
  onClose?: () => void                         // none on X1's /entities/:id body: no Close, no Escape (12.1; M63)
  headingLevel?: HeadingLevel                  // default 2; X1's /entities/:id body passes 1
}

type ContentProps = Omit<EntityPanelProps, 'node' | 'onClose'>
type BodyProps = Omit<ContentProps, 'selection'> & { selection: GraphSelection }

function usableText(value: unknown): string | null {
  return typeof value === 'string' && value !== '' ? value : null
}

/**
 * The counts are the entity page's own: relationships with supporting evidence in the selected
 * releases, before any evidence filter, never the network's figures.
 */
function EntityBody(props: BodyProps) {
  const { entityRef, selection, vocabularies, presentation, onSelect, headingLevel = 2 } = props
  const page = useGraphEntity(entityRef, selection)
  // A retry of a query that holds no data reads as a first load in TanStack Query 5, so only this
  // body's own "Try again" can say that it started the fetch and that the error offered a retry.
  const [retried, setRetried] = useState<unknown>(null)
  // Cleared once the fetch settles, so a fetch no "Try again" started reads as a first load.
  if (retried !== null && page.fetchStatus === 'idle') setRetried(null)
  // The states are exclusive and the first that holds decides, so their order is the spec's.
  let status = ''
  let retry: (() => void) | undefined
  let busy = false
  if (page.data === undefined) {
    const fetching = page.fetchStatus === 'fetching'
    const paused = page.fetchStatus === 'paused'
    if (fetching && retried === null) {
      status = PANEL_LOADING.entity
    } else if ((fetching || paused) && retried !== null) {
      status = PANEL_LOADING.entity
      retry = () => {}
      busy = true
    } else if (page.isError && page.fetchStatus === 'idle') {
      status = panelErrorFor(page.error, 'entity')
      if (panelMayRetry(classifyGraphError(page.error))) {
        const failure = page.error
        retry = () => {
          setRetried(failure)
          void page.refetch()
        }
      }
    } else if (paused && retried === null) {
      // Offline: TanStack Query's default network mode holds the fetch until the connection returns.
      status = PANEL_LOADING.entity
    } else if (page.fetchStatus === 'idle' && page.error === null) {
      // A disabled query: the hook disables a ref that is not one path segment.
      status = panelErrorText({ kind: 'unknown' }, 'entity')
    }
  } else if (!isEntityBody(page.data)) {
    status = panelErrorText({ kind: 'bad_body', status: 200 }, 'entity')
  } else if (page.isRefetchError) {
    // TanStack Query keeps a loaded page beside the failure of its re-read until a fetch succeeds, so
    // the page stays and its line states the failure: a page re-read after a withdrawal must never
    // read as fresh.
    status = panelErrorFor(page.error, 'entity')
    if (panelMayRetry(classifyGraphError(page.error))) {
      retry = () => {
        void page.refetch()
      }
      // Offline the re-read waits for the connection, and a second press would cancel it and start another.
      busy = page.isFetching || page.isPaused
    }
  }
  const body = page.data !== undefined && isEntityBody(page.data) ? page.data : null
  const Sub = `h${subLevel(headingLevel)}` as const
  // Mounted in every state, so a later error or retry is announced.
  const statusLine = <PanelStatus subject="entity" text={status} onRetry={retry} busy={busy} />
  if (body === null) return <>{statusLine}</>
  const entity = body.entity
  const references = arrayOf(body.reference_relationships)
  const nodes = arrayOf(body.nodes)
  const releases = arrayOf(body.releases)
  const counts: Readonly<Record<string, number>> = typeof body.relationship_counts === 'object' && body.relationship_counts !== null
    ? body.relationship_counts
    : {}
  const countKeys = Object.keys(counts)
  const definition = usableText(arrayOf(vocabularies?.entity_types).find((entry) => entry.slug === entity.type)?.definition)
  const taxon = typeof entity.taxon === 'number' ? arrayOf(vocabularies?.taxa).find((entry) => entry.taxid === entity.taxon) : undefined
  const createdBy = usableText(body.created_by_release)
  const description = usableText(body.description)
  // Every list of buttons is named apart, since two reference relationships can share a name.
  const names = uniqueNames(references.map((edge) => ({ id: edge.id, name: relationshipName(edge, nodes, presentation, vocabularies) })))
  return (
    <>
      {statusLine}
      <div data-selectable>
        {/* An entity carries no synthetic flag, so the releases its page lists say it. */}
        {isSyntheticShown({ releases, flags: references.map((edge) => edge.is_synthetic) }) && <p>{LEGEND_TEXT.synthetic}</p>}
        <p>{endName(entity, presentation, vocabularies)}</p>
        {/* A merged entity's redirect is followed, so the panel names the survivor beside the ref asked for. */}
        {entity.id !== entityRef && <p>{mergedText(entityRef)}</p>}
        <p>{entityStatusSentence(entity.status)}</p>
        <p>
          {ENTITY_TERMS.type}: {entityTypeLabel(entity.type, vocabularies)}
          {definition !== null && <>. {clipText(definition)}</>}
        </p>
        {typeof entity.taxon === 'number' && (
          <p>{`${ENTITY_TERMS.taxon}: ${taxon === undefined ? `taxid ${entity.taxon}` : `${clipText(taxon.scientific_name)} (taxid ${entity.taxon})`}`}</p>
        )}
        {description !== null && <p>{clipText(description)}</p>}
        {createdBy !== null && (
          <p>{`${ENTITY_TERMS.createdBy} ${rowReleaseWords(releases.find((release) => release.id === createdBy), createdBy)}`}</p>
        )}
        <Sub style={SUBHEADING_STYLE}>{PANEL_LABELS.identifiers}</Sub>
        <table>
          <caption>{PANEL_LABELS.identifiers}</caption>
          <thead>
            <tr>
              <th scope="col" style={CELL_STYLE}>{ENTITY_TERMS.namespace}</th>
              <th scope="col" style={CELL_STYLE}>{ENTITY_TERMS.value}</th>
              <th scope="col" style={CELL_STYLE}>{ENTITY_TERMS.version}</th>
              <th scope="col" style={CELL_STYLE}>{ENTITY_TERMS.role}</th>
              <th scope="col" style={CELL_STYLE}>{ENTITY_TERMS.link}</th>
            </tr>
          </thead>
          <tbody>
            {arrayOf(body.identifiers).map((identifier, index) => {
              const namespace = usableText(arrayOf(vocabularies?.namespaces).find((entry) => entry.slug === identifier.namespace)?.label)
              const role = usableText(arrayOf(vocabularies?.identifier_roles).find((entry) => entry.value === identifier.role)?.label)
              const url = identifier.url
              return (
                <tr key={index}>
                  <td style={CELL_STYLE}>{namespace ?? clipText(identifier.namespace)}</td>
                  <th scope="row" style={CELL_STYLE}>{clipText(identifier.value)}</th>
                  <td style={CELL_STYLE}>{typeof identifier.version === 'string' ? clipText(identifier.version) : ''}</td>
                  <td style={CELL_STYLE}>{role ?? slugWords(identifier.role)}</td>
                  <td style={CELL_STYLE}>
                    {/* Any other address, a javascript: one among them, is shown as text and never reaches href. */}
                    {isHttpUrl(url)
                      ? <a href={url} target="_blank" rel="noopener noreferrer" style={LINK_STYLE}>{clipText(url)}</a>
                      : usableText(url) !== null ? clipText(String(url)) : ''}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
        <Sub style={SUBHEADING_STYLE}>{PANEL_LABELS.relationshipCounts}</Sub>
        {countKeys.length === 0
          ? <p>{NO_RELATIONSHIP_COUNTS}</p>
          : (
            <>
              <p>{RELATIONSHIP_COUNTS_NOTE}</p>
              {countKeys.map((slug) => <p key={slug}>{clipText(`${typeLabel(slug, vocabularies)}: ${counts[slug]}`)}</p>)}
            </>
          )}
        <Sub style={SUBHEADING_STYLE}>{PANEL_LABELS.referenceRelationships}</Sub>
        {references.length === 0
          ? <p>{NO_REFERENCE_RELATIONSHIPS}</p>
          : (
            <ul role="list">
              {references.map((edge, index) => (
                <li key={edge.id}>
                  <button
                    type="button"
                    // The entity page's selection and no filters, so its summary agrees with this row, and
                    // the page's nodes, so its panel is named as this button until its own page loads.
                    onClick={() => onSelect?.({ kind: 'edge', id: edge.id, record: edge, params: selectionOf(body.query), nodes })}
                    style={LINK_BUTTON_STYLE}
                  >
                    {names[index]}
                  </button>
                </li>
              ))}
            </ul>
          )}
        <Sub style={SUBHEADING_STYLE}>{PANEL_LABELS.releases}</Sub>
        <ReleaseList releases={releases} />
      </div>
    </>
  )
}

/** The body mounts only after the debounce, through a timer: toggling `enabled` would send a stored 503 again. */
function EntityContent(props: ContentProps) {
  const { entityRef, selection, vocabularies, presentation, onSelect, headingLevel } = props
  const ready = usePanelDelay()
  // An entity's record has no summary view: the heading names it.
  if (selection === null || !ready) return <p>{selection === null ? PANEL_WAITING : PANEL_LOADING.entity}</p>
  return (
    <EntityBody
      entityRef={entityRef}
      selection={selection}
      vocabularies={vocabularies}
      presentation={presentation}
      onSelect={onSelect}
      headingLevel={headingLevel}
    />
  )
}

export function EntityPanel(props: EntityPanelProps) {
  const { entityRef, selection, node, vocabularies, presentation, onSelect, onClose, headingLevel = 2 } = props
  return (
    <PanelShell
      heading={entityHeading(entityRef, node ?? null, presentation, vocabularies)}
      subject="entity"
      bodyKey={selection === null ? '' : panelParamsKey(selection)}
      headingLevel={headingLevel}
      onClose={onClose}
    >
      <EntityContent
        entityRef={entityRef}
        selection={selection}
        vocabularies={vocabularies}
        presentation={presentation}
        onSelect={onSelect}
        headingLevel={headingLevel}
      />
    </PanelShell>
  )
}
