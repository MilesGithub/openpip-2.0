import { Suspense, lazy } from 'react'
import { BROWSERS_NOT_ENABLED, BROWSERS_TITLE } from '../graph/pages/pageWords'
import { TypedFeatureGate } from '../graph/pages/TypedFeatureGate'
import { TypedRouteLoading } from '../graph/pages/TypedRouteStates'

const GalleryPage = lazy(() => import('./GalleryPage').then((module) => ({ default: module.GalleryPage })))
const InstancePage = lazy(() => import('./InstancePage').then((module) => ({ default: module.InstancePage })))

export function BrowsersGalleryRoute() {
  return (
    <TypedFeatureGate feature="browsers" title={BROWSERS_TITLE} notEnabled={BROWSERS_NOT_ENABLED}>
      <Suspense key="browsers-gallery" fallback={<TypedRouteLoading title={BROWSERS_TITLE} />}>
        <GalleryPage />
      </Suspense>
    </TypedFeatureGate>
  )
}

export function BrowserInstanceRoute() {
  return (
    <TypedFeatureGate feature="browsers" title={BROWSERS_TITLE} notEnabled={BROWSERS_NOT_ENABLED}>
      <Suspense key="browsers-instance" fallback={<TypedRouteLoading title={BROWSERS_TITLE} />}>
        <InstancePage />
      </Suspense>
    </TypedFeatureGate>
  )
}
