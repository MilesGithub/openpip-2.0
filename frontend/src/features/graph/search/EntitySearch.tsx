import { useEffect, useEffectEvent, useId, useState } from 'react'
import type { FormEvent, RefObject } from 'react'
import { useQueries } from '@tanstack/react-query'
import { classifyGraphError } from '../../../api/graph/errors'
import { normalizeResolveParams } from '../../../api/graph/params'
import type { GraphResolveParams, GraphSelection } from '../../../api/graph/params'
import { useGraphViewer } from '../../../api/graph/request'
import { chunkResolveTerms, graphResolveOptions } from '../../../api/graph/resolve'
import { useGraphStatus } from '../../../api/graph/status'
import { SyntheticBadge } from '../badge/SyntheticBadge'
import { SYNTHETIC_EXPLAINED } from '../badge/badgeWords'
import { own } from '../network/graph'
import { useRetry, useStatusRecheck } from '../pages/pageHooks'
import {
  FIELD_STYLE, FORM_ROW_STYLE, INPUT_STYLE, PAGE_BUTTON_OFF_STYLE, PAGE_BUTTON_STYLE, PLAIN_LIST_STYLE, STATE_LINE_STYLE,
} from '../pages/pageStyle'
import { TypedRouteStatus } from '../pages/TypedRouteStates'
import { endName, uniqueNames } from '../panels/panelWords'
import { isSyntheticShown } from '../panels/releases'
import type { GraphCandidate, GraphEntityRef, GraphNode, GraphVocabularies } from '../types'
import { taxonName } from '../words/taxa'
import {
  autoAdds, candidateMatch, candidateOrganism, chunkOutcome, entityTypeOptions, searchLimits, searchMayRetry, searchTaxonOptions,
  splitSearch, termOutcomes,
} from './searchModel'
import type { SplitSearch, TermEntry, TermResult } from './searchModel'
import {
  ANY_ENTITY_TYPE, CANDIDATE_LIST_STYLE, CHOSEN_MARK, ENTITY_TYPE_LABEL, FACTS_STYLE, RESULTS_NAME, RESULT_LINE_STYLE, SEARCH_ANY_ORGANISM,
  SEARCH_BUTTON, SEARCH_FORM_NAME, SEARCH_TAXON_LABEL, TERMS_BOX_STYLE, TERMS_FIELD_STYLE, TERMS_HINT, TERMS_LABEL, addText,
  candidateFactsText, matchesName, searchStatusText, termLineText,
} from './searchWords'
import type { TermContext } from './searchWords'

export interface EntitySearchProps {
  selection: GraphSelection                         // the view's releases or browser; {} for neither (Q27)
  chosen: readonly GraphEntityRef[]                 // the seeds already chosen, in their order
  maxChosen: number                                 // the host's seed limit, exploreLimits(status).maxSeeds
  onAdd: (entities: readonly GraphNode[]) => void   // one call per landing or per click: the host writes once (7.4)
  vocabularies?: GraphVocabularies | null
  taxon?: number                                    // given: sent with every search, and no organism select shows
  entityType?: string                               // given: sent with every search, and no entity type select shows
  inputRef?: RefObject<HTMLTextAreaElement | null>  // the host moves focus here after a removal (section 8)
  badged?: boolean                                  // true: the host shows the synthetic badge, so the search shows none (Q25)
}

interface SearchSubmit { entries: TermEntry[]; over: number; params: GraphResolveParams; serial: number }
interface SearchLanding { key: string; serial: number; chunks: number[] }

export function EntitySearch(props: EntitySearchProps) {
  const { selection, chosen, maxChosen, onAdd, vocabularies = null, taxon, entityType, inputRef, badged = false } = props
  const boxId = useId()
  const hintId = useId()
  const typeId = useId()
  const taxonId = useId()
  const viewer = useGraphViewer()
  const limits = searchLimits(useGraphStatus({ enabled: false }).data)
  const [draft, setDraft] = useState('')
  const [typeChoice, setTypeChoice] = useState('')
  const [taxonChoice, setTaxonChoice] = useState('')
  const [submitted, setSubmitted] = useState<SearchSubmit | null>(null)
  const [landed, setLanded] = useState<SearchLanding | null>(null)
  const [picked, setPicked] = useState<Readonly<Record<string, GraphEntityRef>>>({})
  // One request per chunk per submit, never per keystroke (J44): F1's chunks, side by side (Q8, Q27).
  const params = submitted === null ? null : submitted.params
  const chunks = params === null || params.q.length === 0 ? [] : chunkResolveTerms(params, limits.maxTerms)
  const queries = useQueries({
    queries: params === null ? [] : chunks.map((chunk) => graphResolveOptions(viewer, { ...params, q: chunk })),
  })
  const outcomes = queries.map((query, index) => chunkOutcome({
    data: query.data, error: query.error, isFetching: query.isFetching || query.isPaused, size: chunks[index].length,
  }))
  const split: SplitSearch = submitted === null
    ? { entries: [], sent: [], over: 0 }
    : { entries: submitted.entries, sent: [...submitted.params.q], over: submitted.over }
  const results = termOutcomes(split, chunks, outcomes)
  // The releases the answered chunks drew on: the badge's condition while no network is drawn (Q25).
  const searchReleases = outcomes.flatMap((outcome) => (outcome.kind === 'answered' ? outcome.releases : []))
  const serial = submitted === null ? 0 : submitted.serial
  // A landing: every chunk has answered or failed. The adds wait for it, so one submit makes one write.
  const settled = outcomes.length > 0 && outcomes.every((outcome) => outcome.kind !== 'searching')
  const answered = outcomes.flatMap((outcome, index) => (outcome.kind === 'answered' ? [index] : []))
  const landing = settled ? `${serial}:${answered.join(',')}` : null
  // The chunks answered since the last landing whose adds reached the address: a retried chunk adds its own terms only.
  const fresh = landed !== null && landed.serial === serial ? answered.filter((index) => !landed.chunks.includes(index)) : answered
  const freshAdds = autoAdds(
    results.filter((result) => result.chunk !== null && fresh.includes(result.chunk)).map((result) => result.outcome),
    chosen,
    maxChosen,
  )
  const add = useEffectEvent(() => {
    if (freshAdds.length > 0) onAdd(freshAdds)
  })
  useEffect(() => {
    if (landing !== null) add()
  }, [landing])
  // A not_found, which every typed route but status answers once the switch is off, rechecks status once (J13).
  let notFound: { error: unknown; errorUpdatedAt: number } = { error: null, errorUpdatedAt: 0 }
  for (const query of queries) {
    if (query.error !== null && query.error !== undefined && classifyGraphError(query.error).kind === 'not_found'
      && query.errorUpdatedAt > notFound.errorUpdatedAt) notFound = query
  }
  useStatusRecheck(notFound)
  const failed = queries.filter((_query, index) => outcomes[index].kind === 'error')
  const retry = useRetry(
    {
      refetch: () => Promise.all(failed.map((query) => query.refetch())),
      isFetching: queries.some((query) => query.isFetching),
      isPaused: queries.some((query) => query.isPaused),
    },
    `search:${serial}`,
    outcomes.some((outcome) => outcome.kind === 'error' && searchMayRetry(outcome.error)),
  )
  // Guarded and during render, since a state set in an effect fails lint: once this landing's adds have reached `chosen`
  // or met the limit, its terms no longer read as being added.
  if (landing !== null && landed?.key !== landing && freshAdds.length === 0) {
    // Every chunk that has landed since this submit stays recorded, so a chunk whose background refetch failed adds
    // nothing again once retried.
    const before = landed !== null && landed.serial === serial ? landed.chunks : []
    setLanded({ key: landing, serial, chunks: Array.from(new Set([...before, ...answered])) })
  }

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const next = splitSearch(draft, limits)
    if (next.entries.length === 0) return
    const snapshot: GraphResolveParams = {
      ...selection,
      q: next.sent,
      taxon: taxon ?? (taxonChoice === '' ? undefined : Number(taxonChoice)),
      entity_type: entityType ?? (typeChoice === '' ? undefined : typeChoice),
    }
    setPicked({})
    setSubmitted({ entries: next.entries, over: next.over, params: snapshot, serial: serial + 1 })
    // An unchanged key sends nothing by itself, so a failed chunk is asked again (F4's BR/SeedSearch.tsx:73-78).
    if (params !== null && JSON.stringify(normalizeResolveParams(snapshot)) === JSON.stringify(normalizeResolveParams(params))) {
      queries.forEach((query, index) => {
        if (outcomes[index].kind === 'error') void query.refetch()
      })
    }
  }
  const choose = (term: string, entity: GraphNode, off: boolean) => {
    if (off) return
    setPicked((previous) => ({ ...previous, [term]: entity.id }))
    onAdd([entity])
  }

  const atLimit = chosen.length >= maxChosen
  const organism = params === null || params.taxon === undefined ? null : taxonName(params.taxon, vocabularies)
  const recorded = landing === null || landed?.key !== landing
  const scoped = params !== null && (params.releases !== undefined || params.browser !== undefined)
  // A candidate list's names: each candidate's endName, made distinct (G/panels/panelWords.ts:206-210).
  const namesOf = (candidates: readonly GraphCandidate[]) =>
    uniqueNames(candidates.map((candidate) => ({ id: candidate.entity.id, name: endName(candidate.entity, undefined, vocabularies) })))
  // The term's context for termLineText (7.11, 7.12).
  const contextOf = (result: TermResult): TermContext => {
    const outcome = result.outcome
    const entity = outcome.kind === 'resolved' ? outcome.candidate.entity : null
    const isChosen = entity !== null && chosen.includes(entity.id)
    // The term is typed text, so a lookup by it goes through own (2.1).
    const pickedId = own(picked, result.entry.term)
    const candidates = outcome.kind === 'choose' ? outcome.candidates : []
    const pickedAt = pickedId === undefined || !chosen.includes(pickedId)
      ? -1
      : candidates.findIndex((candidate) => candidate.entity.id === pickedId)
    return {
      name: entity === null ? null : endName(entity, undefined, vocabularies),
      chosen: isChosen,
      atLimit,
      max: maxChosen,
      pending: result.chunk !== null && fresh.includes(result.chunk) && recorded && !isChosen && !atLimit,
      pickedName: pickedAt === -1 ? null : namesOf(candidates)[pickedAt],
      organism,
      scoped,
      maxTermChars: limits.maxTermChars,
    }
  }
  const contexts = results.map(contextOf)
  const resolvedContexts = contexts.filter((_context, index) => results[index].outcome.kind === 'resolved')
  const failedCount = results.filter((result) => result.outcome.kind === 'failed').length
  const firstError = outcomes.find((outcome) => outcome.kind === 'error')
  const line = searchStatusText({
    submitted: submitted !== null,
    searching: outcomes.some((outcome) => outcome.kind === 'searching'),
    searched: split.sent.length - failedCount,
    over: split.over,
    maxTerms: limits.maxTerms,
    cut: outcomes.reduce((sum, outcome) => sum + (outcome.kind === 'answered' ? outcome.cut : 0), 0),
    failed: failedCount,
    failure: firstError !== undefined && firstError.kind === 'error' ? firstError.error : undefined,
    chosen: resolvedContexts.filter((context) => context.chosen).length,
    notAdded: resolvedContexts.filter((context) => !context.chosen && context.atLimit).length,
    adding: resolvedContexts.filter((context) => !context.chosen && !context.atLimit && context.pending).length,
    choose: results.filter((result) => result.outcome.kind === 'choose').length,
    unmatched: results.filter((result) => result.outcome.kind === 'unmatched').length,
  })
  // The candidate list, for choose only (7.11).
  const candidateList = (term: string, candidates: readonly GraphCandidate[]) => {
    const names = namesOf(candidates)
    return (
      <ul role="list" aria-label={matchesName(term)} style={{ ...PLAIN_LIST_STYLE, ...CANDIDATE_LIST_STYLE }}>
        {candidates.map((candidate, index) => {
          const candidateChosen = chosen.includes(candidate.entity.id)
          const off = candidateChosen || atLimit
          const facts = candidateFactsText(candidateOrganism(candidate, vocabularies), candidateMatch(candidate))
          return (
            <li key={candidate.entity.id}>
              <button type="button" aria-disabled={off ? 'true' : undefined} onClick={() => choose(term, candidate.entity, off)}
                style={off ? PAGE_BUTTON_OFF_STYLE : PAGE_BUTTON_STYLE}>{addText(names[index])}</button>
              {candidateChosen && CHOSEN_MARK}
              {facts !== '' && <p style={FACTS_STYLE}>{facts}</p>}
            </li>
          )
        })}
      </ul>
    )
  }
  return (
    <>
      <form role="search" aria-label={SEARCH_FORM_NAME} onSubmit={submit} style={FORM_ROW_STYLE}>
        <div style={TERMS_FIELD_STYLE}>
          <label htmlFor={boxId}>{TERMS_LABEL}</label>
          <textarea id={boxId} ref={inputRef} rows={3} value={draft} aria-describedby={hintId}
            onChange={(event) => setDraft(event.target.value)} style={TERMS_BOX_STYLE} />
          <p id={hintId} style={STATE_LINE_STYLE}>{TERMS_HINT}</p>
        </div>
        {entityType === undefined && (
          <div style={FIELD_STYLE}>
            <label htmlFor={typeId}>{ENTITY_TYPE_LABEL}</label>
            <select id={typeId} value={typeChoice} onChange={(event) => setTypeChoice(event.target.value)} style={INPUT_STYLE}>
              <option value="">{ANY_ENTITY_TYPE}</option>
              {entityTypeOptions(vocabularies).map((option) => <option key={option.value} value={option.value}>{option.text}</option>)}
            </select>
          </div>
        )}
        {taxon === undefined && (
          <div style={FIELD_STYLE}>
            <label htmlFor={taxonId}>{SEARCH_TAXON_LABEL}</label>
            <select id={taxonId} value={taxonChoice} onChange={(event) => setTaxonChoice(event.target.value)} style={INPUT_STYLE}>
              <option value="">{SEARCH_ANY_ORGANISM}</option>
              {searchTaxonOptions(vocabularies).map((option) => <option key={option.value} value={option.value}>{option.text}</option>)}
            </select>
          </div>
        )}
        <button type="submit" style={PAGE_BUTTON_STYLE}>{SEARCH_BUTTON}</button>
      </form>
      <TypedRouteStatus text={line} onRetry={retry.offered ? retry.onRetry : undefined} busy={retry.busy} />
      {!badged && isSyntheticShown({ releases: searchReleases }) && <p><SyntheticBadge /> {SYNTHETIC_EXPLAINED}</p>}
      {results.length > 0 && (
        <ul role="list" aria-label={RESULTS_NAME} style={PLAIN_LIST_STYLE}>
          {results.map((result) => (
            <li key={result.entry.term}>
              <p style={RESULT_LINE_STYLE}>{termLineText(result.entry.term, result.outcome, contextOf(result))}</p>
              {result.outcome.kind === 'choose' && candidateList(result.entry.term, result.outcome.candidates)}
            </li>
          ))}
        </ul>
      )}
    </>
  )
}
