import type { RouteObject } from 'react-router-dom'
import { BrowserInstanceRoute, BrowsersGalleryRoute } from '../../browsers/BrowsersRoutes'
import { TypedRouteError } from './TypedRouteStates'

/** The typed pages' routes, appended once to the root route's children (App.tsx). X1 and C1 append theirs here. */
export const TYPED_ROUTES: RouteObject[] = [
  { path: 'browsers', Component: BrowsersGalleryRoute, ErrorBoundary: TypedRouteError },
  { path: 'browsers/:slug', Component: BrowserInstanceRoute, ErrorBoundary: TypedRouteError },
]
