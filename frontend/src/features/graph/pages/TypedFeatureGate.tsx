import { useRef } from 'react'
import type { ReactNode } from 'react'
import { graphFeatureState, useGraphStatus } from '../../../api/graph/status'
import type { GraphFeatureName } from '../types'
import { useRetry } from './pageHooks'
import { PAGE_STYLE } from './pageStyle'
import { gateView } from './routeState'
import { PageHeading, TypedRouteStatus } from './TypedRouteStates'

export interface TypedFeatureGateProps {
  feature: GraphFeatureName   // F4: 'browsers' (J14)
  title: string               // the h1 of the loading, off and unknown states
  notEnabled: string          // the off sentence, the only words that say "not enabled on this portal"
  children: ReactNode         // rendered only while the feature is on
}
export function TypedFeatureGate(props: TypedFeatureGateProps) {
  const { feature, title, notEnabled, children } = props
  // The children mount only on `on`, so a page's data hooks are enabled only then and its chunk loads no
  // sooner (J12, J13). This observer keeps the one five-minute re-read of `status`; the Navbar's sets none (J6).
  const status = useGraphStatus()
  const state = graphFeatureState(status, feature)
  const view = gateView(state, notEnabled)
  const retry = useRetry(status, 'status', view?.retry ?? false)
  const headingRef = useRef<HTMLHeadingElement | null>(null)
  if (view === null) return <>{children}</>
  return (
    <div style={PAGE_STYLE}>
      <PageHeading headingRef={headingRef} ready={state !== 'loading'}>{title}</PageHeading>
      <TypedRouteStatus text={view.text} onRetry={retry.offered ? retry.onRetry : undefined} busy={retry.busy} />
    </div>
  )
}
