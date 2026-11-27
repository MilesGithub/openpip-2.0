import { useRef } from 'react'
import { Link, useLocation, useSearchParams } from 'react-router-dom'
import { useArrivalScroll } from '../graph/pages/pageHooks'
import { PAGE_STYLE } from '../graph/pages/pageStyle'
import { PageHeading } from '../graph/pages/TypedRouteStates'
import { LINK_STYLE } from '../graph/panels/text'
import { exploreSearchString, withoutExploreNames } from '../graph/viewState'
import type { EntitySelectionName } from './entityAddress'
import { selectionRemovalText } from './entityWords'

export interface AddressProblemProps {
  title: string                                // the page's one h1 (J43): ENTITY_TITLE or ENTITY_LOOKUP_TITLE
  text: string                                 // what the address cannot give, a word of 10.10
  removal?: readonly EntitySelectionName[]     // given: a link to this address without these names (J16)
}
export function AddressProblem(props: AddressProblemProps) {
  const { title, text, removal = [] } = props
  // An arrival scrolls to the top and the h1 takes focus once (J30); nothing is sent while the address stands.
  useArrivalScroll()
  const headingRef = useRef<HTMLHeadingElement | null>(null)
  const location = useLocation()
  const [searchParams] = useSearchParams()
  return (
    <div style={PAGE_STYLE}>
      <PageHeading headingRef={headingRef}>{title}</PageHeading>
      <p>{text}</p>
      {removal.length > 0 && (
        <p>
          <Link to={{ pathname: location.pathname, search: exploreSearchString(withoutExploreNames(searchParams, removal)) }}
            onClick={() => headingRef.current?.focus()} style={LINK_STYLE}>
            {selectionRemovalText(removal)}
          </Link>
        </p>
      )}
    </div>
  )
}
