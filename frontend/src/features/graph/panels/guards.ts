import type { GraphEntityResponse, GraphEvidenceResponse, GraphRelationshipResponse, GraphRelationshipRef } from '../types'

// The client accepts any JSON object with status 200, so a body that lacks its shape gets the
// bad-body words instead of reaching the builders.

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

/** The selected ref, so a body for another relationship never shows under this one's heading. */
export function isRelationshipBody(data: unknown, ref: GraphRelationshipRef): data is GraphRelationshipResponse {
  return isObject(data) && isObject(data.edge) && data.edge.id === ref
}

/** Any string id: a merged entity's 301 is followed, so the id may differ from the one asked for. */
export function isEntityBody(data: unknown): data is GraphEntityResponse {
  return isObject(data) && isObject(data.entity) && typeof data.entity.id === 'string'
}

/** A page's rows are `evidence`; it has no `results` key. */
export function isEvidencePage(page: unknown): page is GraphEvidenceResponse {
  return isObject(page) && Array.isArray(page.evidence) && isObject(page.page)
}
