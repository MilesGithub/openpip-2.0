import { useRef } from 'react'
import { Link, useLocation, useSearchParams } from 'react-router-dom'
import { useGraphStatus } from '../../api/graph/status'
import { useArrivalScroll } from '../graph/pages/pageHooks'
import { PAGE_STYLE } from '../graph/pages/pageStyle'
import { EXPLORE_TITLE } from '../graph/pages/pageWords'
import { PageHeading } from '../graph/pages/TypedRouteStates'
import { LINK_STYLE } from '../graph/panels/text'
import {
  EXPLORE_PATH, EXPLORE_VIEW_VERSION, exploreLimits, exploreSearchString, exploreViewFromSearch, exploreWrite, searchOfExploreView,
  withoutExploreNames,
} from '../graph/viewState'
import type { ExploreChange, ExploreSelectedRef, ExploreView } from '../graph/viewState'
import { Explorer } from './Explorer'
import { exploreBackOf } from './exploreModel'
import {
  NEWER_VERSION, NEWER_VERSION_LINK, UNREADABLE_VERSION, addressProblemText, addressRemovalText, backText,
} from './exploreWords'

export function ExplorePage() {
  const headingRef = useRef<HTMLHeadingElement | null>(null)
  const location = useLocation()
  const [searchParams, setSearchParams] = useSearchParams()
  const status = useGraphStatus({ enabled: false })
  // Read on every render: Back and Forward keep the page mounted, and a value read once would go stale (Q26).
  const address = exploreViewFromSearch(searchParams, exploreLimits(status.data))
  // An arrival scrolls to the top once, but not while the address restores a selection, whose panel's heading takes focus
  // on mount (8.6); a write keeps the page mounted and does not scroll (J30).
  useArrivalScroll(address.selected === null)
  const back = exploreBackOf(location.state)
  // One write per event, holding the whole view and the workspace's selection (7.4; Q34).
  const write = (next: ExploreView, change: ExploreChange, selected: ExploreSelectedRef | null) => {
    setSearchParams(searchOfExploreView(next, selected, searchParams), exploreWrite(location.state, change))
  }
  return (
    <div style={PAGE_STYLE}>
      <PageHeading headingRef={headingRef}>{EXPLORE_TITLE}</PageHeading>
      {back !== null && (
        <p><Link to={{ pathname: back.pathname, search: back.search }} state={back.state} style={LINK_STYLE}>{backText(back.title)}</Link></p>
      )}
      {address.version !== EXPLORE_VIEW_VERSION ? (
        <>
          <p>{address.version === 'other' ? NEWER_VERSION : UNREADABLE_VERSION}</p>
          <p>
            <Link to={EXPLORE_PATH} state={location.state} onClick={() => headingRef.current?.focus()} style={LINK_STYLE}>
              {NEWER_VERSION_LINK}
            </Link>
          </p>
        </>
      ) : address.invalid.length > 0 ? (
        <>
          <p>{addressProblemText(address.invalid)}</p>
          <p>
            <Link to={{ pathname: EXPLORE_PATH, search: exploreSearchString(withoutExploreNames(searchParams, address.invalid)) }}
              state={location.state} onClick={() => headingRef.current?.focus()} style={LINK_STYLE}>
              {addressRemovalText(address.invalid)}
            </Link>
          </p>
        </>
      ) : (
        <Explorer view={address.view} onViewChange={write} selected={address.selected}
          onSelectedChange={(selected) => write(address.view, 'selected', selected)} headingLevel={2} />
      )}
    </div>
  )
}
