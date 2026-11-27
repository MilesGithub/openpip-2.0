import { Suspense, lazy } from 'react'
import { ENTITY_LOOKUP_NOT_ENABLED, ENTITY_LOOKUP_TITLE, ENTITY_NOT_ENABLED, ENTITY_TITLE } from '../graph/pages/pageWords'
import { TypedFeatureGate } from '../graph/pages/TypedFeatureGate'
import { TypedRouteLoading } from '../graph/pages/TypedRouteStates'

// One lazy module holds both pages, so the entity page a lookup redirects to has loaded already and shows no fallback.
const EntityPages = lazy(() => import('./EntityPages').then((module) => ({ default: module.EntityPages })))

export function EntityRoute() {
  return (
    <TypedFeatureGate feature="entities" title={ENTITY_TITLE} notEnabled={ENTITY_NOT_ENABLED}>
      <Suspense key="entity" fallback={<TypedRouteLoading title={ENTITY_TITLE} />}>
        <EntityPages page="entity" />
      </Suspense>
    </TypedFeatureGate>
  )
}

export function EntityLookupRoute() {
  return (
    <TypedFeatureGate feature="resolve" title={ENTITY_LOOKUP_TITLE} notEnabled={ENTITY_LOOKUP_NOT_ENABLED}>
      <Suspense key="entity-lookup" fallback={<TypedRouteLoading title={ENTITY_LOOKUP_TITLE} />}>
        <EntityPages page="lookup" />
      </Suspense>
    </TypedFeatureGate>
  )
}
