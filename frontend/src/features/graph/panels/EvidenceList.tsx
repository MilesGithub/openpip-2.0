import { Fragment, useEffect, useId, useMemo, useRef, useState } from 'react'
import { classifyGraphError } from '../../../api/graph/errors'
import type {
  GraphEdge, GraphNode, GraphOutcome, GraphPresentation, GraphRelease, GraphReleaseCounts, GraphVocabularies,
} from '../types'
import { evidencePagesOf, evidenceRowView, evidenceTotal, laterPageRequested } from './evidence'
import type { EvidenceQueryResult, EvidenceRowView } from './evidence'
import {
  EVIDENCE_EMPTY, PANEL_LABELS, PANEL_LOADING, RAW_UNAVAILABLE, ROW_TERMS, byReleaseLine, moreColumnsText, panelErrorFor,
  panelErrorText, panelMayRetry, shownCount,
} from './panelWords'
import { rowReleaseWords } from './releases'
import { BUTTON_STYLE, DETAIL_STYLE, LINK_STYLE, SOURCE_TEXT_STYLE, TERM_STYLE } from './text'

export interface EvidenceListProps {
  evidence: EvidenceQueryResult                  // the body's useGraphEvidence result (10; M41)
  edge: GraphEdge | null                         // the shown edge (shownEdgeOf, 9): the total and the type's words; null while none is known (M5)
  byRelease?: readonly GraphReleaseCounts[] | null
  requested: readonly GraphOutcome[]             // requestedOf(params)
  releases: readonly GraphRelease[]              // the merged list, to word each row's release
  nodes: readonly GraphNode[]                    // the page's nodes, else the kept graph's merged with the selection's (3)
  vocabularies?: GraphVocabularies | null
  presentation?: GraphPresentation | null
}

/** Assertion counts: no independent count per release exists. */
function resultCount(counts: Readonly<Record<string, unknown>> | null | undefined): number {
  let total = 0
  for (const count of Object.values(counts ?? {})) {
    if (typeof count === 'number') total += count
  }
  return total
}

/**
 * Collapsed rows hold no raw text in the document: a page can carry more than two million raw
 * characters, and one record any number of columns. A button, since user-event never stops on a
 * summary element; its name adds the record field, which names the row, so no two toggles share one.
 */
function RawRecord({ view }: { view: EvidenceRowView }) {
  const [open, setOpen] = useState(false)
  const id = useId()
  const raw = view.raw
  if (raw.kind !== 'columns') return null
  return (
    <>
      <button
        type="button"
        aria-expanded={open}
        aria-controls={id}
        aria-label={`${PANEL_LABELS.rawToggle}: ${view.record}`}
        onClick={() => setOpen(!open)}
        style={BUTTON_STYLE}
      >
        {PANEL_LABELS.rawToggle}
      </button>
      {open && (
        <>
          <dl id={id} data-selectable>
            {raw.entries.map(([key, value], index) => (
              <Fragment key={index}>
                <dt style={TERM_STYLE}>{key}</dt>
                <dd style={{ ...DETAIL_STYLE, ...SOURCE_TEXT_STYLE }}>{value}</dd>
              </Fragment>
            ))}
          </dl>
          {raw.hidden > 0 && <p>{moreColumnsText(raw.hidden)}</p>}
        </>
      )}
    </>
  )
}

function EvidenceRowItem({ view }: { view: EvidenceRowView }) {
  return (
    <li>
      <p><strong>{view.title}</strong></p>
      <dl data-selectable>
        {view.fields.map((field) => (
          <Fragment key={field.field}>
            <dt style={TERM_STYLE}>{field.term}</dt>
            <dd style={DETAIL_STYLE}>
              {field.lines.map((line, index) => (line.href !== undefined
                ? <a key={index} href={line.href} target="_blank" rel="noopener noreferrer" style={LINK_STYLE}>{line.text}</a>
                : <span key={index} style={{ display: 'block', ...SOURCE_TEXT_STYLE }}>{line.text}</span>))}
            </dd>
          </Fragment>
        ))}
        <dt style={TERM_STYLE}>{ROW_TERMS.record}</dt>
        <dd style={DETAIL_STYLE}>
          <span style={{ display: 'block', ...SOURCE_TEXT_STYLE }}>{view.record}</span>
          {view.raw.kind === 'unavailable' && <span style={{ display: 'block' }}>{RAW_UNAVAILABLE}</span>}
          {view.raw.kind === 'columns' && <RawRecord view={view} />}
        </dd>
      </dl>
    </li>
  )
}

/**
 * The relationship body owns the query, so one observer reads it and its releases reach the
 * release list without lifting state. Messages sit in one region and the controls after it, so a
 * message is announced without a button's name, and the count line sits just before the control
 * that changes it.
 */
export function EvidenceList(props: EvidenceListProps) {
  const { evidence, edge, byRelease, requested, releases, nodes, vocabularies, presentation } = props
  const pages = evidencePagesOf(evidence.data?.pages, evidence.data?.pageParams)
  const releaseMap = useMemo(() => new Map(releases.map((release) => [release.id, release] as const)), [releases])
  const context = useMemo(
    () => ({ edge, releases: releaseMap, requested, nodes, presentation, vocabularies }),
    [edge, releaseMap, requested, nodes, presentation, vocabularies],
  )
  const views = pages.rows.map((row) => evidenceRowView(row, context))
  const listRef = useRef<HTMLDivElement | null>(null)
  const countRef = useRef<HTMLParagraphElement | null>(null)
  const retryFocused = useRef(false)
  const pagingFocused = useRef(false)
  // A list with no page reads as a first load while it is fetched again, so only the list's own
  // first-page "Try again" can say that it started the fetch.
  const [retried, setRetried] = useState<unknown>(null)
  // `evidence` is the body's result, a prop: the render the press causes still holds the body's last
  // result, idle and holding the stored error, until the body hears of the running fetch a task
  // later. Cleared on the fetch status alone, the press would never show its busy row.
  if (retried !== null && evidence.fetchStatus === 'idle' && evidence.error !== retried) setRetried(null)
  // A fetch over loaded pages keeps the stored error until it settles, and the flags name the
  // running fetch's direction, not the failure's: "Show more" after a failed re-read would read as a
  // failed later page, and a re-read after a failed later page as a failed re-read. Once the query is
  // idle they name the fetch that stored the error, so the list keeps their reading and holds it
  // through every fetch it sees start. A list that mounts during a fetch has seen none settle: an HTTP
  // or network failure names the request it answered, which places it, though a re-read that failed
  // on a request sent with the last loaded page's `next` reads as a later page's; an error that cannot
  // say is placed by the first result's flags, which still name the failed fetch unless a fetch begun
  // before the mount reset them.
  const [pageFailure, setPageFailure] = useState<unknown>(() => {
    const requested = evidence.fetchStatus === 'idle' ? null : laterPageRequested(evidence.error, evidence.data?.pages)
    if (requested === null) return evidence.isFetchNextPageError ? evidence.error : null
    return requested ? evidence.error : null
  })
  const settledPageFailure = evidence.isFetchNextPageError ? evidence.error : null
  if (evidence.fetchStatus === 'idle' && settledPageFailure !== pageFailure) setPageFailure(settledPageFailure)
  const laterPageFailed = evidence.isError && evidence.error !== null && evidence.error === pageFailure

  // The states are exclusive and the first that holds decides, so their order is the spec's.
  const hasData = evidence.data !== undefined
  let words = ''
  let retry: (() => void) | undefined
  let retryBusy = false
  let paging = false
  if (!hasData) {
    const fetching = evidence.fetchStatus === 'fetching'
    const paused = evidence.fetchStatus === 'paused'
    if (fetching && retried === null) {
      words = PANEL_LOADING.evidence
    } else if ((fetching || paused) && retried !== null) {
      words = PANEL_LOADING.evidence
      retry = () => {}
      retryBusy = true
    } else if (evidence.isError && evidence.fetchStatus === 'idle') {
      words = panelErrorFor(evidence.error, 'evidence')
      if (panelMayRetry(classifyGraphError(evidence.error))) {
        const failure = evidence.error
        retry = () => {
          setRetried(failure)
          void evidence.refetch()
        }
      }
    } else if (paused && retried === null) {
      // Offline: TanStack Query's default network mode holds the fetch until the connection returns.
      words = PANEL_LOADING.evidence
    } else if (evidence.fetchStatus === 'idle' && evidence.error === null) {
      // A disabled query: the hook disables a ref that is not one path segment.
      words = panelErrorText({ kind: 'unknown' }, 'evidence')
    }
  } else if (pages.bad) {
    words = panelErrorText({ kind: 'bad_body', status: 200 }, 'evidence')
  } else {
    if (evidence.isFetchingNextPage) words = PANEL_LOADING.evidenceMore
    // Offline, a later page or a re-read of the loaded pages waits for the connection, and the
    // paused flag does not say which, so the words name neither.
    else if (evidence.isPaused) words = PANEL_LOADING.evidence
    else if (laterPageFailed) words = panelErrorFor(evidence.error, 'evidence')
    // Following a cursor already sent would grow the list for ever with rows of one key.
    else if (pages.repeated) words = panelErrorText({ kind: 'bad_body', status: 200 }, 'evidence')
    if (evidence.isRefetchError && !laterPageFailed) {
      words = panelErrorFor(evidence.error, 'evidence')
      if (panelMayRetry(classifyGraphError(evidence.error))) {
        retry = () => {
          void evidence.refetch()
        }
        // Offline the re-read waits for the connection, and a second press would cancel it and start another.
        retryBusy = evidence.isFetching || evidence.isPaused
      }
    }
    // A later page refused with a kind a repeat cannot change leaves no control that repeats it.
    paging = evidence.hasNextPage && !pages.repeated
      && (!laterPageFailed || panelMayRetry(classifyGraphError(evidence.error)))
  }
  // The end is a null `next` only, never a row count: a short page, or an empty one with a `next`,
  // is not the end.
  const countLine = hasData && !pages.bad
    ? (pages.rows.length === 0 && !evidence.hasNextPage ? EVIDENCE_EMPTY : shownCount(pages.rows.length, evidenceTotal(edge)))
    : null
  const offered = retry !== undefined
  // A control that goes while it had focus hands focus to the count line just before it, else to
  // the panel's heading, so focus never drops on the page's body.
  useEffect(() => {
    if (offered || !retryFocused.current) return
    retryFocused.current = false
    const count = countRef.current
    if (count !== null && count.isConnected) {
      count.focus()
      return
    }
    const id = listRef.current?.closest('section')?.getAttribute('aria-labelledby')
    const heading = typeof id === 'string' && id !== '' ? document.getElementById(id) : null
    heading?.focus()
  }, [offered])
  useEffect(() => {
    if (paging || !pagingFocused.current) return
    pagingFocused.current = false
    const count = countRef.current
    if (count !== null && count.isConnected) {
      count.focus()
      return
    }
    const id = listRef.current?.closest('section')?.getAttribute('aria-labelledby')
    const heading = typeof id === 'string' && id !== '' ? document.getElementById(id) : null
    heading?.focus()
  }, [paging])
  // Never `disabled`, which takes focus away in a browser: while any fetch of the list runs or waits
  // offline the control is only marked busy, and its handler returns at once. During a re-read of the
  // loaded pages a press would only be handed the running re-read, sending nothing, and so be lost.
  const pagingBusy = evidence.isFetching || evidence.isPaused
  return (
    <div ref={listRef} style={{ width: '100%' }}>
      {byRelease !== undefined && byRelease !== null && byRelease.length > 0 && (
        <ul role="list" style={{ listStyle: 'none', margin: 0, padding: 0 }}>
          {byRelease.map((entry, index) => (
            <li key={index}>
              {byReleaseLine(rowReleaseWords(releaseMap.get(entry.release), entry.release), resultCount(entry.outcome_counts))}
            </li>
          ))}
        </ul>
      )}
      <ul role="list" style={{ listStyle: 'none', margin: 0, padding: 0 }}>
        {views.map((view) => <EvidenceRowItem key={view.id} view={view} />)}
      </ul>
      <div role="status">
        {countLine !== null && <p ref={countRef} tabIndex={-1}>{countLine}</p>}
        {words !== '' && <p>{words}</p>}
      </div>
      {retry !== undefined && (
        <button
          type="button"
          aria-disabled={retryBusy ? 'true' : undefined}
          onFocus={() => {
            retryFocused.current = true
          }}
          onBlur={() => {
            retryFocused.current = false
          }}
          onClick={() => {
            if (retryBusy) return
            retry?.()
          }}
          style={retryBusy ? { ...BUTTON_STYLE, cursor: 'default' } : BUTTON_STYLE}
        >
          {PANEL_LABELS.tryAgain.evidence}
        </button>
      )}
      {paging && (
        <button
          type="button"
          aria-disabled={pagingBusy ? 'true' : undefined}
          onFocus={() => {
            pagingFocused.current = true
          }}
          onBlur={() => {
            pagingFocused.current = false
          }}
          onClick={() => {
            if (pagingBusy) return
            // After a failure this sends the failed page's cursor again; a refetch would re-read the
            // loaded pages and never the failed one. A second call returns the running fetch.
            void evidence.fetchNextPage({ cancelRefetch: false })
          }}
          style={pagingBusy ? { ...BUTTON_STYLE, cursor: 'default' } : BUTTON_STYLE}
        >
          {laterPageFailed ? PANEL_LABELS.tryAgain.evidence : PANEL_LABELS.showMore}
        </button>
      )}
    </div>
  )
}
