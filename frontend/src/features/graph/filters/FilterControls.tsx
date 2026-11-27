import { useEffect, useId, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import type { TypedGraph } from '../network/graph'
import { FIELD_STYLE, FORM_ROW_STYLE, INPUT_STYLE, PAGE_BUTTON_STYLE, STATE_LINE_STYLE, STATUS_LINE_STYLE } from '../pages/pageStyle'
import type { GraphVocabularies } from '../types'
import type { ExploreChange, ExploreLimits, ExploreView } from '../viewState'
import {
  ANY_ORGANISM, CHECK_LABEL_STYLE, CLEAR_FILTERS, CONTROL_ROW_STYLE, DIRECTION_NOTE, EFFECT_NOTE, FILTERS_NAME, FOLLOW_LABEL,
  FOLLOW_NOTE, GROUP_STYLE, LAYERS_LOADING, LAYERS_MINIMUM, LAYER_TYPES_STYLE, LEGEND_STYLE, METHOD_APPLY, METHOD_HINT, OUTCOME_NOTE,
  SCORE_NOTE, TAXON_NOTE, TOGGLES_NAME, layerText, methodProblemText, releasesLimitText, typesGroupName,
} from './filterWords'
import { layerModel, withLayerFamily, withLayerType, withoutLayerExtra } from './layers'
import {
  EXPLORE_FILTERS, basisOptions, directionOptions, effectOptions, isFiltered, methodsOfText, methodsText, releaseNotes,
  releaseOptions, scoreTypeOptions, taxonOptions, withDirection, withFilterValue, withTaxon, withoutFilters,
} from './registry'
import type { CheckFilterId, CheckOption } from './registry'

export interface ExploreControlsProps {
  view: ExploreView
  onViewChange: (next: ExploreView, change: ExploreChange) => void   // the host writes the address once (7.4)
}
export interface LayerControlsProps extends ExploreControlsProps {
  vocabularies: GraphVocabularies | null
  graph: TypedGraph | null   // the workspace's graph: the counts' source
  show: boolean              // the workspace's show, which allOutcomes controls (Q30, Q33)
}
export interface FilterControlsProps extends ExploreControlsProps {
  vocabularies: GraphVocabularies | null
  graph: TypedGraph | null   // the workspace's graph: the release options' source
  limits: ExploreLimits      // exploreLimits(status): the release cap
}

export function ExploreToggles(props: ExploreControlsProps) {
  const { view, onViewChange } = props
  const followNote = useId()
  const outcomeNote = useId()
  return (
    <div role="group" aria-label={TOGGLES_NAME} style={CONTROL_ROW_STYLE}>
      <div style={FIELD_STYLE}>
        <label style={CHECK_LABEL_STYLE}>
          <input type="checkbox" checked={view.follow} aria-describedby={followNote}
            onChange={() => onViewChange({ ...view, follow: !view.follow }, 'follow')} />
          {' '}{FOLLOW_LABEL}
        </label>
        <p id={followNote} style={STATE_LINE_STYLE}>{FOLLOW_NOTE}</p>
      </div>
      <div style={FIELD_STYLE}>
        <label style={CHECK_LABEL_STYLE}>
          <input type="checkbox" checked={view.allOutcomes} aria-describedby={outcomeNote}
            onChange={() => onViewChange({ ...view, allOutcomes: !view.allOutcomes }, 'outcome')} />
          {' '}{EXPLORE_FILTERS.outcome.label}
        </label>
        <p id={outcomeNote} style={STATE_LINE_STYLE}>{OUTCOME_NOTE}</p>
      </div>
    </div>
  )
}

interface FamilyCheckboxProps { checked: boolean; mixed: boolean; off: boolean; describedBy: string; onToggle: () => void }

function FamilyCheckbox(props: FamilyCheckboxProps) {
  const { checked, mixed, off, describedBy, onToggle } = props
  const box = useRef<HTMLInputElement | null>(null)
  useEffect(() => {
    if (box.current !== null) box.current.indeterminate = mixed
  }, [mixed])
  return (
    <input ref={box} type="checkbox" checked={checked} aria-disabled={off ? 'true' : undefined} aria-describedby={off ? describedBy : undefined}
      onChange={() => { if (!off) onToggle() }} />
  )
}

export function LayerControls(props: LayerControlsProps) {
  const { view, onViewChange, vocabularies, graph, show } = props
  const model = layerModel(view, vocabularies, graph, show)
  const change = (next: ExploreView) => { if (next !== view) onViewChange(next, 'layers') }
  const layersRef = useRef<HTMLFieldSetElement | null>(null)
  const minimumId = useId()
  return (
    <fieldset ref={layersRef} tabIndex={-1} style={GROUP_STYLE}>
      <legend style={LEGEND_STYLE}>{EXPLORE_FILTERS.type.label}</legend>
      {vocabularies === null && <p style={STATE_LINE_STYLE}>{LAYERS_LOADING}</p>}
      {model.families.map((family) => (
        <div key={family.family}>
          <label style={CHECK_LABEL_STYLE}>
            <FamilyCheckbox checked={family.state === 'all'} mixed={family.state === 'some'} off={family.held && family.state === 'all'}
              describedBy={minimumId} onToggle={() => change(withLayerFamily(view, model, family.family, family.state !== 'all'))} />
            {' '}{layerText(family.text, family.count, family.state !== 'off')}
          </label>
          {family.types.length > 1 && (
            <div role="group" aria-label={typesGroupName(family.text)} style={LAYER_TYPES_STYLE}>
              {family.types.map((type) => (
                <label key={type.slug} style={CHECK_LABEL_STYLE}>
                  <input type="checkbox" checked={type.on} aria-disabled={type.held ? 'true' : undefined}
                    aria-describedby={type.held ? minimumId : undefined}
                    onChange={() => { if (!type.held) change(withLayerType(view, model, type.slug, !type.on)) }} />
                  {' '}{layerText(type.text, type.count, type.on)}
                </label>
              ))}
            </div>
          )}
        </div>
      ))}
      {model.extras.map((extra) => (
        <label key={extra.piece} style={CHECK_LABEL_STYLE}>
          <input type="checkbox" checked
            onChange={() => { layersRef.current?.focus(); change(withoutLayerExtra(view, model, extra.piece)) }} />
          {' '}{extra.text}
        </label>
      ))}
      {model.held && <p id={minimumId} style={STATE_LINE_STYLE}>{LAYERS_MINIMUM}</p>}
    </fieldset>
  )
}

interface CheckboxGroupProps {
  legend: string
  notes: readonly string[]
  offNote?: string
  options: readonly CheckOption[]
  onToggle: (value: string, on: boolean) => void
  moveFocusFirst: (option: CheckOption, on: boolean) => boolean
}

function CheckboxGroup(props: CheckboxGroupProps) {
  const { legend, notes, offNote, options, onToggle, moveFocusFirst } = props
  const groupRef = useRef<HTMLFieldSetElement | null>(null)
  const offNoteId = useId()
  return (
    <fieldset ref={groupRef} tabIndex={-1} style={GROUP_STYLE}>
      <legend style={LEGEND_STYLE}>{legend}</legend>
      {notes.map((note) => <p key={note} id={note === offNote ? offNoteId : undefined} style={STATE_LINE_STYLE}>{note}</p>)}
      {options.map((option) => (
        <label key={option.value} style={CHECK_LABEL_STYLE}>
          <input type="checkbox" checked={option.checked} aria-disabled={option.off ? 'true' : undefined}
            aria-describedby={option.off ? offNoteId : undefined}
            onChange={() => {
              if (option.off) return
              if (moveFocusFirst(option, !option.checked)) groupRef.current?.focus()
              onToggle(option.value, !option.checked)
            }} />
          {' '}{option.text}
        </label>
      ))}
    </fieldset>
  )
}

function MethodBox(props: ExploreControlsProps) {
  const { view, onViewChange } = props
  const id = useId()
  const hintId = useId()
  const problemId = useId()
  const source = methodsText(view.method)
  const [draft, setDraft] = useState<{ source: string; text: string; problem: string | null }>({ source, text: source, problem: null })
  // The box follows Back and Forward: a draft typed for another address gives way to this one's methods, through a guarded
  // update during render, since a state set in an effect fails lint (BR/GalleryPage.tsx:90-94).
  const current = draft.source === source ? draft : { source, text: source, problem: null }
  if (draft.source !== source) setDraft(current)
  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const read = methodsOfText(current.text)
    if (read.problem !== null) {
      setDraft({ ...current, problem: read.problem })
      return
    }
    const next = read.methods.length === 0 ? null : read.methods
    setDraft({ source, text: methodsText(next), problem: null })
    if (methodsText(next) !== source) onViewChange({ ...view, method: next }, 'method')
  }
  return (
    <div style={FIELD_STYLE}>
      <form onSubmit={submit} style={FORM_ROW_STYLE}>
        <div style={FIELD_STYLE}>
          <label htmlFor={id}>{EXPLORE_FILTERS.method.label}</label>
          <input id={id} type="text" value={current.text} aria-invalid={current.problem === null ? undefined : 'true'}
            aria-describedby={current.problem === null ? hintId : `${hintId} ${problemId}`}
            onChange={(event) => setDraft({ ...current, text: event.target.value })} style={INPUT_STYLE} />
        </div>
        <button type="submit" style={PAGE_BUTTON_STYLE}>{METHOD_APPLY}</button>
      </form>
      <p id={hintId} style={STATE_LINE_STYLE}>{METHOD_HINT}</p>
      <p id={problemId} role="status" style={STATUS_LINE_STYLE}>{current.problem === null ? '' : methodProblemText(current.problem)}</p>
    </div>
  )
}

export function FilterControls(props: FilterControlsProps) {
  const { view, onViewChange, vocabularies, graph, limits } = props
  const directionId = useId()
  const directionNote = useId()
  const taxonId = useId()
  const taxonNote = useId()
  const first = useRef<HTMLSelectElement | null>(null)
  const toggle = (id: CheckFilterId) => (value: string, on: boolean) => onViewChange(withFilterValue(view, id, value, on), EXPLORE_FILTERS[id].change)
  const bases = basisOptions(view, vocabularies)
  const scores = scoreTypeOptions(view, vocabularies)
  const releases = releaseOptions(view, graph, limits)
  const appendedGoes = (option: CheckOption, on: boolean) => !on && option.appended
  const releaseGoes = (_option: CheckOption, on: boolean) => !on
  return (
    <div role="group" aria-label={FILTERS_NAME} style={CONTROL_ROW_STYLE}>
      <div style={FIELD_STYLE}>
        <label htmlFor={directionId}>{EXPLORE_FILTERS.direction.label}</label>
        <select id={directionId} ref={first} value={view.direction ?? 'both'} aria-describedby={directionNote}
          onChange={(event) => onViewChange(withDirection(view, event.target.value), 'direction')} style={INPUT_STYLE}>
          {directionOptions().map((option) => <option key={option.value} value={option.value}>{option.text}</option>)}
        </select>
        <p id={directionNote} style={STATE_LINE_STYLE}>{DIRECTION_NOTE}</p>
      </div>
      <CheckboxGroup legend={EXPLORE_FILTERS.effect.label} notes={[EFFECT_NOTE]} options={effectOptions(view)} onToggle={toggle('effect')}
        moveFocusFirst={appendedGoes} />
      {bases.length > 0 && (
        <CheckboxGroup legend={EXPLORE_FILTERS.basis.label} notes={[]} options={bases} onToggle={toggle('basis')} moveFocusFirst={appendedGoes} />
      )}
      <MethodBox view={view} onViewChange={onViewChange} />
      {scores.length > 0 && (
        <CheckboxGroup legend={EXPLORE_FILTERS.score_type.label} notes={[SCORE_NOTE]} options={scores} onToggle={toggle('score_type')}
          moveFocusFirst={appendedGoes} />
      )}
      <div style={FIELD_STYLE}>
        <label htmlFor={taxonId}>{EXPLORE_FILTERS.taxon.label}</label>
        <select id={taxonId} value={view.taxon === null ? '' : String(view.taxon)} aria-describedby={taxonNote}
          onChange={(event) => onViewChange(withTaxon(view, event.target.value), 'taxon')} style={INPUT_STYLE}>
          <option value="">{ANY_ORGANISM}</option>
          {taxonOptions(view, vocabularies).map((option) => <option key={option.value} value={option.value}>{option.text}</option>)}
        </select>
        <p id={taxonNote} style={STATE_LINE_STYLE}>{TAXON_NOTE}</p>
      </div>
      <CheckboxGroup legend={EXPLORE_FILTERS.release.label} notes={releaseNotes(view, graph, releases, limits)}
        offNote={releasesLimitText(limits.maxReleases)} options={releases} onToggle={toggle('release')} moveFocusFirst={releaseGoes} />
      {isFiltered(view) && (
        <button type="button" style={PAGE_BUTTON_STYLE} onClick={() => { first.current?.focus(); onViewChange(withoutFilters(view), 'clear') }}>
          {CLEAR_FILTERS}
        </button>
      )}
    </div>
  )
}
