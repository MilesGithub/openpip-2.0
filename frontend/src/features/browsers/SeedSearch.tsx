import { useEffect, useEffectEvent, useId, useRef, useState } from 'react'
import type { FormEvent, RefObject } from 'react'
import type { GraphSelection } from '../../api/graph/params'
import { useGraphResolve } from '../../api/graph/resolve'
import { useStatusRecheck } from '../graph/pages/pageHooks'
import {
  FIELD_STYLE, FORM_ROW_STYLE, INPUT_STYLE, PAGE_BUTTON_OFF_STYLE, PAGE_BUTTON_STYLE, PLAIN_LIST_STYLE, STATUS_LINE_STYLE,
} from '../graph/pages/pageStyle'
import { endName, uniqueNames } from '../graph/panels/panelWords'
import type { GraphEntityRef, GraphNode, GraphPresentation, GraphVocabularies } from '../graph/types'
import { taxonName } from '../graph/words/taxa'
import {
  CANDIDATES_NAME, CHOSEN_MARK, SEED_BUTTON, SEED_FORM_NAME, SEED_LABEL, SEED_TERM_MAX, addText, seedOutcome, seedOutcomeText,
  termProblem,
} from './seedWords'

export interface SeedSearchProps {
  selection: GraphSelection                       // F4: { browser: slug }
  taxon?: number                                  // sent only when given (singleTaxon, 6.2)
  chosen: readonly GraphEntityRef[]               // the seeds already chosen
  maxChosen: number                               // the page's seed limit (10.4)
  onAdd: (entity: GraphNode) => void              // the candidate's node: the page keeps its name, then writes the address once (J1)
  presentation?: GraphPresentation | null
  vocabularies?: GraphVocabularies | null
  inputRef?: RefObject<HTMLInputElement | null>   // the page moves focus here after a removal
}
export function SeedSearch(props: SeedSearchProps) {
  const { selection, taxon, chosen, maxChosen, onAdd, presentation, vocabularies, inputRef } = props
  const id = useId()
  const [draft, setDraft] = useState('')
  const [submitted, setSubmitted] = useState<{ term: string; serial: number } | null>(null)
  const [problem, setProblem] = useState(false)
  const [landed, setLanded] = useState(0)
  const [picked, setPicked] = useState<GraphEntityRef | null>(null)
  // One term per submit, never split and never debounced (J44); a `not_found`, which every typed route but
  // `status` answers once the switch is off, rechecks `status`, so the gate can say so (J13).
  const resolve = useGraphResolve({ ...selection, q: submitted === null ? [] : [submitted.term], taxon }, { enabled: submitted !== null })
  useStatusRecheck(resolve)
  // Offline, the query waits for the network with no data and no error, and its fetch goes out once the network is
  // back: it is still searching, as `useRetry` reads a paused fetch, and a press meanwhile asks nothing again, since a
  // refetch would only join the waiting fetch.
  const running = resolve.isFetching || resolve.isPaused
  const outcome = seedOutcome({
    problem, submitted: submitted !== null, data: resolve.data, error: resolve.error, isFetching: running,
  })
  // A resolved term adds its entity once per submit: a new submit of the same term adds it again from the
  // cache, after a removal or after an add refused at the limit once a seed has gone (J1). The outcome is
  // read through an effect event, so the effect lists only the entity and the submit's serial.
  const resolvedRef = outcome.kind === 'resolved' ? outcome.candidate.entity.id : null
  const serial = submitted === null ? 0 : submitted.serial
  const added = useRef(0)
  const add = useEffectEvent(() => {
    if (outcome.kind === 'resolved') onAdd(outcome.candidate.entity)
  })
  useEffect(() => {
    if (resolvedRef !== null && added.current !== serial) {
      added.current = serial
      add()
    }
  }, [resolvedRef, serial])

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const term = draft.trim()
    if (term === '') return
    if (termProblem(term)) {
      setProblem(true)
      return
    }
    setProblem(false)
    setPicked(null)
    setSubmitted({ term, serial: (submitted?.serial ?? 0) + 1 })
    // An unchanged key sends nothing by itself, so a failed request, or a body the outcome refuses, is asked
    // again here. The query's own outcome leaves `problem` out: a term refused since is no answer to this one.
    const queryOutcome = seedOutcome({
      problem: false, submitted: submitted !== null, data: resolve.data, error: resolve.error, isFetching: running,
    })
    if (submitted !== null && submitted.term === term && queryOutcome.kind === 'error') void resolve.refetch()
  }

  const atLimit = chosen.length >= maxChosen
  const resolvedEntity = outcome.kind === 'resolved' ? outcome.candidate.entity : null
  const resolvedChosen = resolvedEntity !== null && chosen.includes(resolvedEntity.id)
  // Guarded and during render, since a state set in an effect fails lint: once this submit's add has reached
  // `chosen`, or met the limit, the line no longer reads as searching (J1).
  if (outcome.kind === 'resolved' && submitted !== null && landed !== submitted.serial && (resolvedChosen || atLimit)) {
    setLanded(submitted.serial)
  }
  const candidates = outcome.kind === 'choose' ? outcome.candidates : []
  const names = uniqueNames(candidates.map((candidate) => ({
    id: candidate.entity.id, name: endName(candidate.entity, presentation, vocabularies),
  })))
  // The clicked candidate is named only while `chosen` holds it, so a removal takes its sentence away.
  const pickedAt = picked === null || !chosen.includes(picked) ? -1 : candidates.findIndex((candidate) => candidate.entity.id === picked)
  const line = seedOutcomeText(outcome, {
    term: submitted === null ? '' : submitted.term,
    name: resolvedEntity === null ? null : endName(resolvedEntity, presentation, vocabularies),
    chosen: resolvedChosen,
    atLimit,
    max: maxChosen,
    pending: outcome.kind === 'resolved' && submitted !== null && landed !== submitted.serial && !resolvedChosen && !atLimit,
    pickedName: pickedAt === -1 ? null : names[pickedAt],
    organism: taxon === undefined ? null : taxonName(taxon, vocabularies),
  })

  return (
    <>
      <form role="search" aria-label={SEED_FORM_NAME} onSubmit={submit} style={FORM_ROW_STYLE}>
        <div style={FIELD_STYLE}>
          <label htmlFor={id}>{SEED_LABEL}</label>
          <input id={id} ref={inputRef} type="search" maxLength={SEED_TERM_MAX} value={draft}
            onChange={(event) => setDraft(event.target.value)} style={INPUT_STYLE} />
        </div>
        <button type="submit" style={PAGE_BUTTON_STYLE}>{SEED_BUTTON}</button>
      </form>
      <p role="status" style={STATUS_LINE_STYLE}>{line}</p>
      {outcome.kind === 'choose' && (
        <ul role="list" aria-label={CANDIDATES_NAME} style={PLAIN_LIST_STYLE}>
          {candidates.map((candidate, index) => {
            const isChosen = chosen.includes(candidate.entity.id)
            // Off, the button stays mounted with aria-disabled, so focus stays on it once its entity is chosen.
            const off = isChosen || atLimit
            return (
              <li key={candidate.entity.id}>
                <button
                  type="button"
                  aria-disabled={off ? 'true' : undefined}
                  onClick={() => {
                    if (off) return
                    setPicked(candidate.entity.id)
                    onAdd(candidate.entity)
                  }}
                  style={off ? PAGE_BUTTON_OFF_STYLE : PAGE_BUTTON_STYLE}
                >
                  {addText(names[index])}
                </button>
                {isChosen && CHOSEN_MARK}
              </li>
            )
          })}
        </ul>
      )}
    </>
  )
}
