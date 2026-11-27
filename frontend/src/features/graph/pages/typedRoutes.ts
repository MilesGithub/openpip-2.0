import type { RouteObject } from 'react-router-dom'
import { BrowserInstanceRoute, BrowsersGalleryRoute } from '../../browsers/BrowsersRoutes'
import { EntityLookupRoute, EntityRoute } from '../../entities/EntitiesRoutes'
import { ExploreRoute } from '../../explore/ExploreRoutes'
import { TypedRouteError } from './TypedRouteStates'

/** The typed pages' routes, appended once to the root route's children (App.tsx). X1 and C1 append theirs here. */
export const TYPED_ROUTES: RouteObject[] = [
  { path: 'browsers', Component: BrowsersGalleryRoute, ErrorBoundary: TypedRouteError },
  { path: 'browsers/:slug', Component: BrowserInstanceRoute, ErrorBoundary: TypedRouteError },
  // X1: the explorer and the two entity pages (Q22), each a gated, lazy page as the Browsers routes are.
  { path: 'explore', Component: ExploreRoute, ErrorBoundary: TypedRouteError },
  { path: 'entities/:id', Component: EntityRoute, ErrorBoundary: TypedRouteError },
  { path: 'entities/:namespace/:value', Component: EntityLookupRoute, ErrorBoundary: TypedRouteError },
]
