import { useRef } from 'react'
import { Link, Navigate, useLocation, useParams, useSearchParams } from 'react-router-dom'
import type { GraphSelection } from '../../api/graph/params'
import { useGraphResolve } from '../../api/graph/resolve'
import { useGraphStatus } from '../../api/graph/status'
import { useGraphVocabularies } from '../../api/graph/vocabularies'
import { SyntheticBadge } from '../graph/badge/SyntheticBadge'
import { SYNTHETIC_EXPLAINED } from '../graph/badge/badgeWords'
import { useArrivalScroll, useRetry, useStatusRecheck } from '../graph/pages/pageHooks'
import { PAGE_STYLE, PLAIN_LIST_STYLE, STATE_LINE_STYLE } from '../graph/pages/pageStyle'
import { ENTITY_LOOKUP_TITLE } from '../graph/pages/pageWords'
import { PageHeading, TypedRouteStatus } from '../graph/pages/TypedRouteStates'
import { endName, uniqueNames } from '../graph/panels/panelWords'
import { LINK_STYLE } from '../graph/panels/text'
import { candidateMatch, candidateOrganism, searchLimits } from '../graph/search/searchModel'
import { FACTS_STYLE, candidateFactsText } from '../graph/search/searchWords'
import { exploreLimits, exploreSearchString, withoutExploreNames } from '../graph/viewState'
import { AddressProblem } from './AddressProblem'
import { entityHref, entityLookupHref, entitySelectionFromSearch } from './entityAddress'
import { LOOKUP_CANDIDATES_NAME, lookupSubjectText, openText, selectionProblemText, selectionRemovalText } from './entityWords'
import { lookupOutcome, lookupParams, lookupRefusal, lookupState, namespaceLabel } from './lookupModel'
import type { LookupRefusal } from './lookupModel'

export function EntityLookupPage() {
  const { namespace = '', value = '' } = useParams()
  const [searchParams] = useSearchParams()
  const status = useGraphStatus({ enabled: false })
  const address = entitySelectionFromSearch(searchParams, exploreLimits(status.data))
  const maxChars = searchLimits(status.data).maxTermChars
  if (address.invalid.length > 0) {
    return <AddressProblem title={ENTITY_LOOKUP_TITLE} text={selectionProblemText(address.invalid)} removal={address.invalid} />
  }
  // Keyed by the address it reads, so another identifier starts afresh: its request, its retry and its focus.
  return (
    <LookupBody key={entityLookupHref(namespace, value, address.selection)} namespace={namespace} value={value}
      selection={address.selection} refusal={lookupRefusal(value, maxChars)} maxChars={maxChars} />
  )
}

interface LookupBodyProps {
  namespace: string
  value: string
  selection: GraphSelection
  refusal: LookupRefusal | null
  maxChars: number
}

function LookupBody({ namespace, value, selection, refusal, maxChars }: LookupBodyProps) {
  useArrivalScroll()
  const headingRef = useRef<HTMLHeadingElement | null>(null)
  const location = useLocation()
  const [searchParams] = useSearchParams()
  const vocabularies = useGraphVocabularies().data ?? null
  // Sent only once the value passes; a 404, which every typed route but status answers once the switch is off, rechecks
  // status once (J13).
  const resolve = useGraphResolve(lookupParams(namespace, value, selection), { enabled: refusal === null })
  useStatusRecheck(resolve)
  const label = namespaceLabel(namespace, vocabularies)
  const state = lookupState({
    value, label, maxChars, refusal,
    // resolve matches within the releases it is sent, so an unmatched value names them when the address does (Q38).
    scoped: selection.releases !== undefined || selection.browser !== undefined,
    outcome: refusal === null
      ? lookupOutcome({ data: resolve.data, error: resolve.error, isFetching: resolve.isFetching, isPaused: resolve.isPaused })
      : null,
  })
  const retry = useRetry(resolve, `${namespace}/${value}`, state.retry)
  const names = uniqueNames(state.candidates.map((candidate) => ({ id: candidate.entity.id, name: endName(candidate.entity, null, vocabularies) })))
  return (
    <div style={PAGE_STYLE}>
      <PageHeading headingRef={headingRef} ready={state.ready}>{ENTITY_LOOKUP_TITLE}</PageHeading>
      <p style={STATE_LINE_STYLE}>{lookupSubjectText(label, namespace, value)}</p>
      {/* F4's header paragraph (InstancePage.tsx:102), spoken: nothing else on this page says the badge's words. */}
      {state.synthetic && <p><SyntheticBadge /> {SYNTHETIC_EXPLAINED}</p>}
      <TypedRouteStatus text={state.line} onRetry={retry.offered ? retry.onRetry : undefined} busy={retry.busy} focusOnGone={headingRef} />
      {state.candidates.length > 0 && (
        <ul role="list" aria-label={LOOKUP_CANDIDATES_NAME} style={PLAIN_LIST_STYLE}>
          {state.candidates.map((candidate, index) => {
            const facts = candidateFactsText(candidateOrganism(candidate, vocabularies), candidateMatch(candidate))
            return (
              <li key={candidate.entity.id}>
                <Link to={entityHref(candidate.entity.id, selection)} style={LINK_STYLE}>{openText(names[index])}</Link>
                {facts !== '' && <p style={FACTS_STYLE}>{facts}</p>}
              </li>
            )
          })}
        </ul>
      )}
      {state.removal.length > 0 && (
        <p>
          <Link to={{ pathname: location.pathname, search: exploreSearchString(withoutExploreNames(searchParams, state.removal)) }}
            onClick={() => headingRef.current?.focus()} style={LINK_STYLE}>
            {selectionRemovalText(state.removal)}
          </Link>
        </p>
      )}
      {state.redirect !== null && <Navigate replace to={entityHref(state.redirect, selection)} />}
    </div>
  )
}
