import { Suspense, lazy } from 'react'
import { EXPLORE_NOT_ENABLED, EXPLORE_TITLE } from '../graph/pages/pageWords'
import { TypedFeatureGate } from '../graph/pages/TypedFeatureGate'
import { TypedRouteLoading } from '../graph/pages/TypedRouteStates'

const ExplorePage = lazy(() => import('./ExplorePage').then((module) => ({ default: module.ExplorePage })))

export function ExploreRoute() {
  return (
    <TypedFeatureGate feature="network" title={EXPLORE_TITLE} notEnabled={EXPLORE_NOT_ENABLED}>
      <Suspense key="explore" fallback={<TypedRouteLoading title={EXPLORE_TITLE} />}>
        <ExplorePage />
      </Suspense>
    </TypedFeatureGate>
  )
}
