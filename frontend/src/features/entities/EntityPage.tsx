import { useState } from 'react'
import { Link, useLocation, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import type { GraphSelection } from '../../api/graph/params'
import { useGraphEntity } from '../../api/graph/entities'
import { graphFeatureState, useGraphStatus } from '../../api/graph/status'
import { useGraphVocabularies } from '../../api/graph/vocabularies'
import { SyntheticBadge } from '../graph/badge/SyntheticBadge'
import { isArrival, useArrivalScroll, useStatusRecheck } from '../graph/pages/pageHooks'
import { PAGE_HEADING_STYLE, PAGE_STYLE } from '../graph/pages/pageStyle'
import { ENTITY_TITLE } from '../graph/pages/pageWords'
import { EntityPanel } from '../graph/panels/EntityPanel'
import { isEntityBody } from '../graph/panels/guards'
import { RelationshipPanel } from '../graph/panels/RelationshipPanel'
import { isSyntheticShown } from '../graph/panels/releases'
import type { PanelSelection } from '../graph/panels/selection'
import { LINK_STYLE, arrayOf } from '../graph/panels/text'
import type { GraphEntityRef } from '../graph/types'
import { exploreHref, exploreLimits, exploreSeedsView } from '../graph/viewState'
import { AddressProblem } from './AddressProblem'
import { entityHref, entitySelectionFromSearch, isEntityRef } from './entityAddress'
import { ENTITY_ADDRESS_PROBLEM, EXPLORE_ENTITY, selectionProblemText } from './entityWords'

export function EntityPage() {
  // The ref and the selection are read on every render. Each view is keyed by its address, so another entity or another
  // selection starts afresh, its panels, its scroll and its focus included, as F4's instance body is keyed by its slug.
  const { id = '' } = useParams()
  const [searchParams] = useSearchParams()
  const status = useGraphStatus({ enabled: false })
  const address = entitySelectionFromSearch(searchParams, exploreLimits(status.data))
  if (!isEntityRef(id)) return <AddressProblem title={ENTITY_TITLE} text={ENTITY_ADDRESS_PROBLEM} />
  if (address.invalid.length > 0) {
    return <AddressProblem title={ENTITY_TITLE} text={selectionProblemText(address.invalid)} removal={address.invalid} />
  }
  return <EntityView key={entityHref(id, address.selection)} entityRef={id} selection={address.selection} />
}

interface EntityViewProps { entityRef: GraphEntityRef; selection: GraphSelection }

function EntityView({ entityRef, selection }: EntityViewProps) {
  useArrivalScroll()
  const location = useLocation()
  const navigate = useNavigate()
  const status = useGraphStatus({ enabled: false })
  const vocabularies = useGraphVocabularies().data ?? null
  // The page's own read of the entity, under the key the panel's body reads, so one request serves both, but for a 404,
  // 429 or JSON 500 already stored when the body mounts 300 ms later, which its mount sends again (Q9); a failure that
  // lands after the mount is shared, as a success is. A 404 rechecks status once per failure (J13).
  const page = useGraphEntity(entityRef, selection)
  useStatusRecheck(page)
  const [opened, setOpened] = useState<PanelSelection | null>(null)
  const body = isEntityBody(page.data) ? page.data : null
  // A merged entity's 301 is followed, so the body names its survivor and the address stays (FE/api/graph/entities.ts:36).
  const shown = body !== null && isEntityRef(body.entity.id) ? body.entity.id : entityRef
  // isEntityBody checks the entity alone and no boundary stands between this view and the route's error, so an item that
  // is no object is left out here; F3's panel reads the same lists inside its PanelBoundary.
  const synthetic = body !== null && isSyntheticShown({
    releases: arrayOf(body.releases).filter((release) => typeof release === 'object' && release !== null),
    flags: arrayOf(body.reference_relationships)
      .filter((edge) => typeof edge === 'object' && edge !== null).map((edge) => edge.is_synthetic === true),
  })
  const explore = body !== null && graphFeatureState(status, 'network') === 'on'
  // A reference relationship opens below, with this page's selection and no filters (U33).
  const open = (next: PanelSelection | null) => setOpened(next !== null && next.kind === 'edge' ? next : null)
  // An end of that relationship: this entity closes the panel, and another entity opens its own page.
  const openEnd = (end: PanelSelection | null) => {
    if (end === null || end.id === entityRef || end.id === shown) setOpened(null)
    else if (end.kind === 'node' && isEntityRef(end.id)) navigate(entityHref(end.id, selection))
  }
  return (
    <div style={PAGE_STYLE}>
      {/* F3's body opens with these words as a sentence, so the badge is shown and never read twice (J21). */}
      {synthetic && <p aria-hidden="true"><SyntheticBadge /></p>}
      <EntityPanel entityRef={entityRef} selection={selection} node={body === null ? null : body.entity}
        vocabularies={vocabularies} onSelect={open} headingLevel={1} headingStyle={PAGE_HEADING_STYLE}
        arrival={{ arrived: isArrival(location.key), ready: page.status !== 'pending' }} />
      {explore && (
        <p><Link to={exploreHref(exploreSeedsView([shown], selection))} style={LINK_STYLE}>{EXPLORE_ENTITY}</Link></p>
      )}
      {opened !== null && opened.kind === 'edge' && (
        <RelationshipPanel key={opened.id} relationshipRef={opened.id} params={opened.params ?? selection}
          edge={'record' in opened ? opened.record : null} nodes={opened.nodes} vocabularies={vocabularies}
          onSelect={openEnd} onClose={() => setOpened(null)} headingLevel={2} />
      )}
    </div>
  )
}
