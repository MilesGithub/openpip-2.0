import { EntityLookupPage } from './EntityLookupPage'
import { EntityPage } from './EntityPage'

export type EntityPageKind = 'entity' | 'lookup'
export interface EntityPagesProps { page: EntityPageKind }
/** The entity pages' one lazy module (EntitiesRoutes.tsx): each route names the page it shows. */
export function EntityPages(props: EntityPagesProps) {
  return props.page === 'entity' ? <EntityPage /> : <EntityLookupPage />
}
