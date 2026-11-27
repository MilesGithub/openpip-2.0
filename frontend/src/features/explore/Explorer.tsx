import { useId, useRef, useState } from 'react'
import { classifyGraphError } from '../../api/graph/errors'
import { graphFeatureState, useGraphStatus } from '../../api/graph/status'
import { useGraphVocabularies } from '../../api/graph/vocabularies'
import { SyntheticBadge } from '../graph/badge/SyntheticBadge'
import { SYNTHETIC_EXPLAINED } from '../graph/badge/badgeWords'
import { ExploreToggles, FilterControls, LayerControls } from '../graph/filters/FilterControls'
import { keptGraph, own } from '../graph/network/graph'
import { visibleGraph } from '../graph/network/visibility'
import { useRetry, useStatusRecheck } from '../graph/pages/pageHooks'
import { PAGE_BUTTON_STYLE, PLAIN_LIST_STYLE, SECTION_HEADING_STYLE, STATE_LINE_STYLE } from '../graph/pages/pageStyle'
import { routeErrorView } from '../graph/pages/routeState'
import { TypedRouteStatus } from '../graph/pages/TypedRouteStates'
import { selectionOf } from '../graph/panels/panelParams'
import { endName, uniqueNames } from '../graph/panels/panelWords'
import { isSyntheticShown } from '../graph/panels/releases'
import { selectionVisibility } from '../graph/panels/selection'
import type { PanelSelection } from '../graph/panels/selection'
import { TERM_STYLE, subLevel } from '../graph/panels/text'
import type { HeadingLevel } from '../graph/panels/text'
import { EntitySearch } from '../graph/search/EntitySearch'
import type { GraphEntityRef, GraphNode } from '../graph/types'
import {
  exploreLimits, exploreNamesOfProblems, exploreNetworkParams, exploreSelected, exploreValueValid, sameExploreView,
  searchOfExploreView, selectedRef,
} from '../graph/viewState'
import type { ExploreChange, ExploreSelectedRef, ExploreView } from '../graph/viewState'
import { TypedNetworkWorkspace } from '../graph/workspace/TypedNetworkWorkspace'
import { useTypedNetworkWorkspace } from '../graph/workspace/useTypedNetworkWorkspace'
import { expandedSeedLimit, viewSelection, withoutViewNames } from './exploreModel'
import {
  CHOSEN_LABEL, FOLLOW_COST, NETWORK_AGAIN, NETWORK_HEADING, NO_SEED_PROMPT, REMOVE_ALL, SEARCH_HEADING, SEARCH_UNAVAILABLE,
  SEARCH_UNAVAILABLE_EMPTY, VIEW_PROBLEM, removeText, viewProblemText, viewRemovalText,
} from './exploreWords'

export interface ExplorerProps {
  view: ExploreView                     // the address's view (ExplorePage) or a saved one (S1)
  onViewChange: (next: ExploreView, change: ExploreChange, selected: ExploreSelectedRef | null) => void
  selected: ExploreSelectedRef | null   // the address's selection: the workspace's initial, then applied when it changes (8.8)
  onSelectedChange: (selected: ExploreSelectedRef | null) => void
  headingLevel: HeadingLevel            // the level of the Explorer's section headings: 2 under /explore's h1
}

export function Explorer(props: ExplorerProps) {
  const { view, onViewChange, selected, onSelectedChange, headingLevel } = props
  // The cached status, read with no request and no timer (F4 10.4): the caps, the closure's cap and the search's gate (Q21).
  const status = useGraphStatus({ enabled: false })
  const limits = exploreLimits(status.data)
  const maxExpanded = expandedSeedLimit(status.data)
  const searchOn = graphFeatureState(status, 'resolve') === 'on'
  const vocabularies = useGraphVocabularies().data ?? null
  // The view's one network request, null with no seed (Q25; 7.5). The address's selection is the hook's initial, read once,
  // and the tested-absent toggle its controlled showNonSupporting (Q30, Q33, Q34).
  const workspace = useTypedNetworkWorkspace(exploreNetworkParams(view), {
    initial: exploreSelected(selected), showNonSupporting: view.allOutcomes,
  })
  // A not_found, which every typed route but status answers once the switch is off, rechecks status once (J13).
  useStatusRecheck(workspace.result)
  // A failure a repeat can change offers F4's "Try again" (J13), keyed by the request, so another view's fetch never keeps it.
  const failed = workspace.state === 'error' && workspace.error !== null && workspace.error !== undefined
  const retryable = failed && classifyGraphError(workspace.error).kind !== 'bad_request'
    && routeErrorView(workspace.error, { other: '', notFound: null, timeout: '' }).retry
  const networkRetry = useRetry(workspace.result, `network:${JSON.stringify(exploreNetworkParams(view))}`, retryable)
  const searchHeadingId = useId()
  const networkHeadingId = useId()
  const chosenLabelId = useId()
  const searchBox = useRef<HTMLTextAreaElement | null>(null)
  const searchHeading = useRef<HTMLHeadingElement | null>(null)
  const networkHeading = useRef<HTMLHeadingElement | null>(null)
  const [named, setNamed] = useState<Readonly<Record<string, string>>>({})
  // The whole address, view and selection, at the last render. Back and Forward keep the page mounted and change the
  // address, whose selection then goes through select when it differs from the hook's, since the hook reads initial
  // once (Q34). The address is compared with its own last value only: the router applies a write in a transition, so a
  // render can show this component's own update beside the previous address (8.8). Guarded and during render, since a
  // state set in an effect fails lint.
  const addressKey = searchOfExploreView(view, selected).toString()
  const [seen, setSeen] = useState(addressKey)
  // The selection this Explorer restored from the address: the hook's initial, or the last one the update below applied
  // through select. A selection made through the wrapped select is never one (8.8 rule 1).
  const [restored, setRestored] = useState<ExploreSelectedRef | null>(selected)
  if (addressKey !== seen) {
    setSeen(addressKey)
    if (selected !== selectedRef(workspace.selected)) {
      workspace.select(exploreSelected(selected))
      setRestored(selected)
    }
  }
  // Once the network has answered the current view, never a kept answer or one the 300 ms debounce still holds, a
  // restored selection it neither draws nor hides is opened as a panel opened from another panel is, with the view's
  // releases and browser and no filter (F3's selectionOf, U33), so an address written from a panel reproduces that
  // panel, and a ref the caller cannot see shows the panel's own words (Q34, Q35; 8.8 rule 1).
  const kept = workspace.graph === null ? null : keptGraph(workspace.graph).graph
  const visible = kept === null ? null : visibleGraph(kept, { showNonSupporting: workspace.show })
  if (restored !== null && workspace.selected !== null && selectedRef(workspace.selected) === restored && workspace.state === 'ready'
    && selectionVisibility(workspace.selected, visible) === 'absent') {
    setRestored(null)
    workspace.select({ ...workspace.selected, params: selectionOf(workspace.graph?.query) })
  }
  const current = selectedRef(workspace.selected)
  const change = (next: ExploreView, kind: ExploreChange) => onViewChange(next, kind, current)
  const select = (selection: PanelSelection | null) => {
    setRestored(null)
    workspace.select(selection)
    onSelectedChange(selectedRef(selection))
  }
  const add = (entities: readonly GraphNode[]) => {
    const seeds = [...view.seeds]
    const added: Record<string, string> = {}
    for (const entity of entities) {
      // An id outside F4's ref grammar would leave the address unreadable, so it is never written (7.3).
      if (seeds.length >= limits.maxSeeds || seeds.includes(entity.id) || !exploreValueValid('seeds', entity.id, limits)) continue
      seeds.push(entity.id)
      added[entity.id] = endName(entity, null, vocabularies)
    }
    if (seeds.length === view.seeds.length) return
    setNamed((previous) => ({ ...previous, ...added }))
    change({ ...view, seeds }, 'seeds')
  }
  const focusSearch = () => {
    (searchBox.current ?? searchHeading.current)?.focus()
  }
  // With no seed the hook has no graph to clear a selection against, so the empty view's address holds none (8.5).
  const clear = () => {
    focusSearch()
    setRestored(null)
    workspace.select(null)
    onViewChange({ ...view, seeds: [] }, 'seeds', null)
  }
  const remove = (ref: GraphEntityRef) => {
    const seeds = view.seeds.filter((seed) => seed !== ref)
    if (seeds.length === 0) {
      clear()
      return
    }
    focusSearch()
    change({ ...view, seeds }, 'seeds')
  }
  const nodes = workspace.graph === null ? [] : workspace.graph.nodes
  const names = uniqueNames(view.seeds.map((ref) => {
    const node = nodes.find((candidate) => candidate.id === ref)
    return { id: ref, name: node !== undefined ? endName(node, null, vocabularies) : own(named, ref) ?? ref }
  }))
  const releases = workspace.graph === null ? [] : workspace.graph.releases
  const info = failed ? classifyGraphError(workspace.error) : null
  const problemNames = info !== null && info.kind === 'bad_request'
    ? exploreNamesOfProblems(info.errors.flatMap((problem) => (typeof problem?.parameter === 'string' ? [problem.parameter] : [])))
    : null
  // F2 keeps the settled error through its 300 ms debounce, so a 400 that answers a view the address no longer holds
  // offers no removal, which would write the same address again (8.9).
  const removable = problemNames !== null && problemNames.length > 0 && !sameExploreView(withoutViewNames(view, problemNames), view)
  // The first rule that applies (8.9).
  const problemLine = networkRetry.offered && problemNames === null ? NETWORK_AGAIN
    : info !== null && info.kind === 'too_expensive' && view.follow ? FOLLOW_COST
      : problemNames === null ? ''
        : problemNames.length === 0 ? VIEW_PROBLEM
          : removable ? viewProblemText(problemNames) : ''

  const Heading = `h${headingLevel}` as const
  return (
    <>
      {isSyntheticShown({ releases }) && <p><SyntheticBadge /> {SYNTHETIC_EXPLAINED}</p>}
      <section aria-labelledby={searchHeadingId}>
        <Heading id={searchHeadingId} ref={searchHeading} tabIndex={-1} style={SECTION_HEADING_STYLE}>{SEARCH_HEADING}</Heading>
        {searchOn ? (
          <EntitySearch selection={viewSelection(view)} chosen={view.seeds} maxChosen={limits.maxSeeds} onAdd={add}
            vocabularies={vocabularies} inputRef={searchBox} badged={isSyntheticShown({ releases })} />
        ) : (
          <p style={STATE_LINE_STYLE}>{view.seeds.length === 0 ? SEARCH_UNAVAILABLE_EMPTY : SEARCH_UNAVAILABLE}</p>
        )}
        {searchOn && view.seeds.length === 0 && <p style={STATE_LINE_STYLE}>{NO_SEED_PROMPT}</p>}
      </section>
      {view.seeds.length > 0 && (
        <section aria-labelledby={networkHeadingId}>
          <Heading id={networkHeadingId} ref={networkHeading} tabIndex={-1} style={SECTION_HEADING_STYLE}>{NETWORK_HEADING}</Heading>
          <p id={chosenLabelId} style={TERM_STYLE}>{CHOSEN_LABEL}</p>
          <ul role="list" aria-labelledby={chosenLabelId} style={PLAIN_LIST_STYLE}>
            {view.seeds.map((ref, index) => (
              <li key={ref}>
                {names[index]}{' '}
                <button type="button" onClick={() => remove(ref)} style={PAGE_BUTTON_STYLE}>{removeText(names[index])}</button>
              </li>
            ))}
          </ul>
          <button type="button" onClick={clear} style={PAGE_BUTTON_STYLE}>{REMOVE_ALL}</button>
          <ExploreToggles view={view} onViewChange={change} />
          <LayerControls view={view} onViewChange={change} vocabularies={vocabularies} graph={workspace.graph} show={workspace.show} />
          <FilterControls view={view} onViewChange={change} vocabularies={vocabularies} graph={workspace.graph} limits={limits} />
          <TypedRouteStatus text={problemLine} onRetry={networkRetry.offered ? networkRetry.onRetry : undefined}
            busy={networkRetry.busy} focusOnGone={networkHeading} />
          {problemNames !== null && removable && (
            <p>
              <button type="button" style={PAGE_BUTTON_STYLE}
                onClick={() => { networkHeading.current?.focus(); change(withoutViewNames(view, problemNames), 'clear') }}>
                {viewRemovalText(problemNames)}
              </button>
            </p>
          )}
          <TypedNetworkWorkspace workspace={{ ...workspace, select }} vocabularies={vocabularies} headingLevel={subLevel(headingLevel)}
            maxExpandedSeeds={maxExpanded} />
        </section>
      )}
    </>
  )
}
