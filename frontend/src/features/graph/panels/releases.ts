import type { GraphPublication, GraphRelease, GraphReleaseRef } from '../types'
import { own } from '../network/graph'
import { releaseWords, slugWords } from '../network/words'
import { RELEASE_FLAGS, RELEASE_STATUS_WORDS, RELEASE_TERMS } from './panelWords'
import { clipText, isHttpUrl } from './text'

/**
 * A release named by the relationship page and by an evidence page is listed once, in the place it
 * first takes, with the newest page's entry.
 */
export function mergeReleases(lists: readonly (readonly GraphRelease[] | null | undefined)[]): GraphRelease[] {
  const order: string[] = []
  const byId = new Map<string, GraphRelease>()
  for (const list of lists) {
    if (!Array.isArray(list)) continue
    for (const release of list) {
      if (!byId.has(release.id)) order.push(release.id)
      byId.set(release.id, release)
    }
  }
  const merged: GraphRelease[] = []
  for (const id of order) {
    const release = byId.get(id)
    if (release !== undefined) merged.push(release)
  }
  return merged
}

export function rowReleaseWords(release: GraphRelease | undefined, ref: GraphReleaseRef): string {
  if (release === undefined) return ref
  const staged = release.status === 'staged' ? RELEASE_FLAGS.staged : ''
  const synthetic = release.is_synthetic ? RELEASE_FLAGS.synthetic : ''
  return clipText(`${releaseWords(release)}${staged}${synthetic}`)
}

export interface ReleaseLine { term: string; text: string; href?: string }

function stated(value: string | null | undefined): string | null {
  return typeof value === 'string' && value !== '' ? value : null
}

/**
 * Licence, attribution and note of changes together, as CC BY and CC BY-SA require. A licence
 * address that is not http(s), a `javascript:` one among them, is shown as text and never reaches
 * `href`: hiding it would drop what the source gave, and text is safe.
 */
export function releaseEntryLines(release: GraphRelease): ReleaseLine[] {
  const scopes = Array.isArray(release.scopes) ? release.scopes : []
  const lines: ReleaseLine[] = [
    { term: RELEASE_TERMS.release, text: clipText(`${release.dataset.title} ${release.version}`) },
    { term: RELEASE_TERMS.status, text: clipText(own(RELEASE_STATUS_WORDS, release.status) ?? slugWords(release.status)) },
    { term: RELEASE_TERMS.scopes, text: clipText(scopes.length === 0 ? 'none' : scopes.map((scope) => slugWords(scope)).join(', ')) },
  ]
  if (release.is_synthetic) lines.push({ term: RELEASE_TERMS.data, text: 'synthetic demonstration data' })
  const licence = release.licence
  const name = stated(licence?.name)
  const spdx = stated(licence?.spdx_id)
  const url = licence?.url
  const named = name !== null && spdx !== null ? `${name} (${spdx})` : name ?? spdx ?? 'no name or SPDX identifier recorded'
  if (isHttpUrl(url)) lines.push({ term: RELEASE_TERMS.licence, text: clipText(named), href: url })
  else if (stated(url) !== null) lines.push({ term: RELEASE_TERMS.licence, text: clipText(`${named} ${url}`) })
  else lines.push({ term: RELEASE_TERMS.licence, text: clipText(named) })
  lines.push({ term: RELEASE_TERMS.attribution, text: clipText(stated(release.attribution) ?? 'none given') })
  lines.push({
    term: RELEASE_TERMS.changes,
    text: clipText(release.changes_note ?? 'not available: the release names no import adapter that this portal has'),
  })
  return lines
}

/** Never from `edge.is_synthetic` alone: it is false when the edge lists no release, even on a synthetic one. */
export function isSyntheticShown(input: {
  releases?: readonly GraphRelease[]
  publications?: readonly GraphPublication[]
  flags?: readonly boolean[]
}): boolean {
  return (input.releases ?? []).some((release) => release.is_synthetic)
    || (input.publications ?? []).some((publication) => publication.is_synthetic)
    || (input.flags ?? []).some((flag) => flag)
}
