import { Fragment, useMemo, useState } from 'react'
import type { GraphRelationshipParams } from '../../../api/graph/params'
import { classifyGraphError } from '../../../api/graph/errors'
import { useGraphEvidence, useGraphRelationship } from '../../../api/graph/relationships'
import type { GraphEdge, GraphNode, GraphPresentation, GraphRelationshipRef, GraphVocabularies } from '../types'
import { EMPTY_GRAPH, keptGraph } from '../network/graph'
import type { TypedGraph } from '../network/graph'
import { edgeReleases } from '../network/words'
import { LEGEND_TEXT } from '../legend/legendText'
import { evidencePagesOf } from './evidence'
import { EvidenceList } from './EvidenceList'
import { isRelationshipBody } from './guards'
import { PanelShell } from './PanelShell'
import { PanelStatus } from './PanelStatus'
import {
  evidenceParamsOf, graphOfRelationship, graphWithNodes, panelParamsKey, requestedOf, usePanelDelay,
} from './panelParams'
import {
  BY_OUTCOME, COUNTING_DEFINITION, COUNTING_LIMITS, EVIDENCE_EVERY_OUTCOME, PANEL_LABELS, PANEL_LOADING, PANEL_WAITING, endName,
  panelErrorFor, panelErrorText, panelMayRetry, relationshipHeading, uniqueNames,
} from './panelWords'
import { ReleaseList } from './ReleaseList'
import { isSyntheticShown, mergeReleases } from './releases'
import type { PanelSelection } from './selection'
import { relationshipSummary, shownEdgeOf, syntheticFlags } from './summary'
import type { RelationshipSummary } from './summary'
import { CELL_STYLE, LINK_BUTTON_STYLE, SUBHEADING_STYLE, arrayOf, clipText, subLevel } from './text'
import type { HeadingLevel } from './text'

export interface RelationshipPanelProps {
  relationshipRef: GraphRelationshipRef
  params: GraphRelationshipParams | null        // relationshipParamsOf(the held query), or the entity page's selection (U33); null: no query seen yet (12.3; M1)
  edge?: GraphEdge | null                       // the kept edge: the record the shell and the summary show until the page loads (U25)
  graph?: TypedGraph | null                     // the network's graph, or while it is null the last one the dispatcher saw (12.3): names and sentence until the page loads
  nodes?: readonly GraphNode[]                  // the selection's nodes: those of the entity page that listed it (4; M9)
  vocabularies?: GraphVocabularies | null
  presentation?: GraphPresentation | null       // node_label_namespace (B1-2); evidence_panels is not read (U5)
  onSelect?: (selection: PanelSelection | null) => void   // an end opens the entity panel (U9)
  onClose?: () => void
  headingLevel?: HeadingLevel                   // default 2
  evidencePageSize?: number                     // tests only; production passes none (U26)
}

type ContentProps = Omit<RelationshipPanelProps, 'onClose'>
type BodyProps = Omit<ContentProps, 'params'> & { params: GraphRelationshipParams }

function typeDefinition(type: string, vocabularies?: GraphVocabularies | null): string {
  const found = arrayOf(vocabularies?.relationship_types).find((entry) => entry.slug === type)?.definition
  return typeof found === 'string' ? found : ''
}

interface Ends { edge: GraphEdge; nodes: readonly GraphNode[]; params: GraphRelationshipParams }

/**
 * In the body it keeps its place across the page's states, so an end button keeps focus when the
 * page arrives. The waiting view passes no ends: its heading and sentence name them as text, so no
 * focused button is removed when the body replaces it.
 */
function RelationshipSummaryView({ summary, level, ends, onSelect, presentation, vocabularies }: {
  summary: RelationshipSummary
  level: HeadingLevel
  ends: Ends | null
  onSelect?: (selection: PanelSelection | null) => void
  presentation?: GraphPresentation | null
  vocabularies?: GraphVocabularies | null
}) {
  const Sub = `h${level}` as const
  const refs = ends === null ? [] : ends.edge.source === ends.edge.target ? [ends.edge.source] : [ends.edge.source, ends.edge.target]
  const endNodes = refs.map((ref) => ({ ref, node: ends === null ? undefined : ends.nodes.find((candidate) => candidate.id === ref) }))
  const named = endNodes.filter((end): end is { ref: string; node: GraphNode } => end.node !== undefined)
  // Labels are not unique, so two ends of one name are told apart by their refs.
  const names = uniqueNames(named.map((end) => ({ id: end.node.id, name: endName(end.node, presentation, vocabularies) })))
  return (
    <>
      <Sub style={SUBHEADING_STYLE}>{PANEL_LABELS.summary}</Sub>
      <p><strong>{summary.typeLabel}</strong>{summary.definition !== '' && <>: {summary.definition}</>}</p>
      <p>{clipText(summary.sentence)}</p>
      {summary.notListed !== null && <p>{summary.notListed}</p>}
      {ends !== null && (
        <p>
          {PANEL_LABELS.ends}
          {endNodes.map((end) => {
            const node = end.node
            if (node === undefined) return <Fragment key={end.ref}>{' '}{clipText(end.ref)}</Fragment>
            const name = names[named.findIndex((candidate) => candidate.ref === end.ref)]
            return (
              <Fragment key={end.ref}>
                {' '}
                <button
                  type="button"
                  onClick={() => onSelect?.({ kind: 'node', id: node.id, record: node, params: ends.params })}
                  style={LINK_BUTTON_STYLE}
                >
                  {name}
                </button>
              </Fragment>
            )
          })}
        </p>
      )}
      {summary.qualifiers.map((line, index) => <p key={index}>{line}</p>)}
      {summary.contradiction.length > 0 && (
        <p style={{ borderLeft: '3px solid var(--danger)', paddingLeft: 8 }}>
          <strong>{PANEL_LABELS.contradiction}:</strong> {summary.contradiction.join(' ')}
        </p>
      )}
      {summary.neither !== null && <p>{summary.neither}</p>}
      {[...summary.effect, ...summary.sign, ...summary.level].map((line, index) => <p key={index}>{line}</p>)}
      <Sub style={SUBHEADING_STYLE}>{PANEL_LABELS.counts}</Sub>
      <p>{summary.headline[0]}<br />{summary.headline[1]}</p>
      <table>
        <caption>{BY_OUTCOME.caption}</caption>
        <thead>
          <tr>
            <th scope="col" style={CELL_STYLE}>{BY_OUTCOME.outcome}</th>
            <th scope="col" style={CELL_STYLE}>{BY_OUTCOME.results}</th>
            <th scope="col" style={CELL_STYLE}>{BY_OUTCOME.independent}</th>
          </tr>
        </thead>
        <tbody>
          {summary.byOutcome.map((row) => (
            <tr key={row.outcome}>
              <th scope="row" style={CELL_STYLE}>{row.label}</th>
              <td style={CELL_STYLE}>{row.results}</td>
              <td style={CELL_STYLE}>{row.independent}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p>{summary.outside}</p>
      <p>{summary.publications}</p>
      <p>{COUNTING_DEFINITION}</p>
      <p>{PANEL_LABELS.countingLimits}</p>
      <ul role="list" style={{ listStyle: 'none', margin: 0, padding: 0 }}>
        {COUNTING_LIMITS.map((limit) => <li key={limit}>{limit}</li>)}
      </ul>
    </>
  )
}

/**
 * One opening sends exactly two requests, one under each of the two per-caller limits. The rows
 * are worded from the shown edge, so they read alike before the page arrives, after it and after
 * it fails: the first evidence page can beat the relationship page, the two being rationed apart.
 */
function RelationshipBody(props: BodyProps) {
  const { relationshipRef, params, edge, graph, nodes, vocabularies, presentation, onSelect, headingLevel = 2, evidencePageSize } = props
  const page = useGraphRelationship(relationshipRef, params)
  // A retry of a query that holds no data reads as a first load in TanStack Query 5, so only this
  // body's own "Try again" can say that it started the fetch and that the error offered a retry.
  const [retried, setRetried] = useState<unknown>(null)
  // Cleared once the fetch settles, so a fetch no "Try again" started, such as a reconnect's
  // refetch, reads as a first load and never as a busy "Try again" the error withholds.
  if (retried !== null && page.fetchStatus === 'idle') setRetried(null)
  const evidenceParams = useMemo(
    () => (evidencePageSize === undefined ? evidenceParamsOf(params) : { ...evidenceParamsOf(params), page_size: evidencePageSize }),
    [params, evidencePageSize],
  )
  const evidence = useGraphEvidence(relationshipRef, evidenceParams)
  const kept = useMemo(() => keptGraph(graph ?? EMPTY_GRAPH).graph, [graph])
  const named = useMemo(() => graphWithNodes(kept, nodes), [kept, nodes])
  const body = isRelationshipBody(page.data, relationshipRef) ? page.data : null
  const shownEdge = shownEdgeOf(body, edge ?? null)
  const pages = evidencePagesOf(evidence.data?.pages, evidence.data?.pageParams)
  const merged = mergeReleases([arrayOf(body?.releases), pages.releases])
  const shownNodes = body !== null ? arrayOf(body.nodes) : named.nodes
  const requested = requestedOf(params)
  const definition = body !== null && typeof body.definition === 'string' && body.definition !== ''
    ? body.definition
    : shownEdge === null ? '' : typeDefinition(shownEdge.type, vocabularies)
  const byRelease = body === null ? null : arrayOf(body.by_release)
  const summary = shownEdge === null ? null : relationshipSummary(shownEdge, {
    graph: body === null ? named : graphOfRelationship(body),
    requested,
    byRelease,
    releases: merged,
    definition,
    presentation,
    vocabularies,
  })
  // The states are exclusive and the first that holds decides, so their order is the spec's. The
  // error row and the retry row both pass onRetry, so the status line keeps one button element
  // across them and focus stays on it.
  let status = ''
  let retry: (() => void) | undefined
  let busy = false
  if (page.data === undefined) {
    const fetching = page.fetchStatus === 'fetching'
    const paused = page.fetchStatus === 'paused'
    if (fetching && retried === null) {
      status = PANEL_LOADING.relationship
    } else if ((fetching || paused) && retried !== null) {
      status = PANEL_LOADING.relationship
      retry = () => {}
      busy = true
    } else if (page.isError && page.fetchStatus === 'idle') {
      status = panelErrorFor(page.error, 'relationship')
      if (panelMayRetry(classifyGraphError(page.error))) {
        const failure = page.error
        retry = () => {
          setRetried(failure)
          void page.refetch()
        }
      }
    } else if (paused && retried === null) {
      // Offline: TanStack Query's default network mode holds the fetch until the connection returns.
      status = PANEL_LOADING.relationship
    } else if (page.fetchStatus === 'idle' && page.error === null) {
      // A disabled query: the hook disables a ref that is not one path segment.
      status = panelErrorText({ kind: 'unknown' }, 'relationship')
    }
  } else if (body === null) {
    status = panelErrorText({ kind: 'bad_body', status: 200 }, 'relationship')
  } else if (page.isRefetchError) {
    // TanStack Query keeps a loaded page beside the failure of its re-read until a fetch succeeds, so
    // the page stays and its line states the failure: a page re-read after a withdrawal must never
    // read as fresh.
    status = panelErrorFor(page.error, 'relationship')
    if (panelMayRetry(classifyGraphError(page.error))) {
      retry = () => {
        void page.refetch()
      }
      // Offline the re-read waits for the connection, and a second press would cancel it and start another.
      busy = page.isFetching || page.isPaused
    }
  }
  const synthetic = isSyntheticShown({ releases: merged, publications: pages.publications, flags: syntheticFlags(shownEdge) })
  const level = subLevel(headingLevel)
  const Sub = `h${level}` as const
  return (
    <>
      {synthetic && <p>{LEGEND_TEXT.synthetic}</p>}
      <PanelStatus subject="relationship" text={status} onRetry={retry} busy={busy} />
      {summary !== null && shownEdge !== null && (
        <RelationshipSummaryView
          summary={summary}
          level={level}
          ends={{ edge: shownEdge, nodes: shownNodes, params }}
          onSelect={onSelect}
          presentation={presentation}
          vocabularies={vocabularies}
        />
      )}
      <Sub style={SUBHEADING_STYLE}>{PANEL_LABELS.evidence}</Sub>
      <p>{EVIDENCE_EVERY_OUTCOME}</p>
      <EvidenceList
        evidence={evidence}
        edge={shownEdge}
        byRelease={byRelease}
        requested={requested}
        releases={merged}
        nodes={shownNodes}
        vocabularies={vocabularies}
        presentation={presentation}
      />
      {merged.length > 0 && (
        <>
          <Sub style={SUBHEADING_STYLE}>{PANEL_LABELS.releases}</Sub>
          <ReleaseList releases={merged} />
        </>
      )}
    </>
  )
}

/** The body mounts only after the debounce, through a timer: toggling `enabled` would send a stored 503 again. */
function RelationshipContent(props: ContentProps) {
  const { relationshipRef, params, edge, graph, nodes, vocabularies, presentation, onSelect, headingLevel = 2, evidencePageSize } = props
  const ready = usePanelDelay()
  const kept = useMemo(() => keptGraph(graph ?? EMPTY_GRAPH).graph, [graph])
  const named = useMemo(() => graphWithNodes(kept, nodes), [kept, nodes])
  if (params !== null && ready) {
    return (
      <RelationshipBody
        relationshipRef={relationshipRef}
        params={params}
        edge={edge}
        graph={graph}
        nodes={nodes}
        vocabularies={vocabularies}
        presentation={presentation}
        onSelect={onSelect}
        headingLevel={headingLevel}
        evidencePageSize={evidencePageSize}
      />
    )
  }
  // No request is sent from here. Every read of the edge goes through shownEdgeOf and
  // syntheticFlags, since a selection restored before its graph has no edge at all.
  const shown = shownEdgeOf(null, edge ?? null)
  const synthetic = isSyntheticShown({ releases: shown === null ? [] : edgeReleases(shown, named), flags: syntheticFlags(shown) })
  const summary = shown === null ? null : relationshipSummary(shown, {
    graph: named,
    requested: params === null ? kept.requestedOutcomes : requestedOf(params),
    byRelease: null,
    releases: [],
    definition: typeDefinition(shown.type, vocabularies),
    presentation,
    vocabularies,
  })
  return (
    <>
      {synthetic && <p>{LEGEND_TEXT.synthetic}</p>}
      <p>{params === null ? PANEL_WAITING : PANEL_LOADING.relationship}</p>
      {summary !== null && (
        <RelationshipSummaryView
          summary={summary}
          level={subLevel(headingLevel)}
          ends={null}
          presentation={presentation}
          vocabularies={vocabularies}
        />
      )}
    </>
  )
}

export function RelationshipPanel(props: RelationshipPanelProps) {
  const {
    relationshipRef, params, edge, graph, nodes, vocabularies, presentation, onSelect, onClose, headingLevel = 2, evidencePageSize,
  } = props
  const kept = useMemo(() => keptGraph(graph ?? EMPTY_GRAPH).graph, [graph])
  // Named from the kept nodes merged with the selection's, so a relationship opened from the entity
  // panel is headed as the button that opened it, before and after its page loads.
  const named = useMemo(() => graphWithNodes(kept, nodes), [kept, nodes])
  return (
    <PanelShell
      heading={relationshipHeading(relationshipRef, edge ?? null, named.nodes, kept.edges, presentation, vocabularies)}
      subject="relationship"
      bodyKey={params === null ? '' : panelParamsKey(params)}
      headingLevel={headingLevel}
      onClose={onClose}
    >
      <RelationshipContent
        relationshipRef={relationshipRef}
        params={params}
        edge={edge}
        graph={graph}
        nodes={nodes}
        vocabularies={vocabularies}
        presentation={presentation}
        onSelect={onSelect}
        headingLevel={headingLevel}
        evidencePageSize={evidencePageSize}
      />
    </PanelShell>
  )
}
