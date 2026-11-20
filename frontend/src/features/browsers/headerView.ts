import type { ReleaseLine } from '../graph/panels/releases'
import { isSyntheticShown, releaseEntryLines, rowReleaseWords } from '../graph/panels/releases'
import { RELEASE_TERMS } from '../graph/panels/panelWords'
import { clipText } from '../graph/panels/text'
import type { GraphBrowserResponse, GraphInstance, GraphVocabularies } from '../graph/types'
import { publicationLines } from '../graph/words/citation'
import type { CitationLine } from '../graph/words/citation'
import { organismTaxids, taxonWords } from '../graph/words/taxa'
import { objectItems, stateWords, stringItems, versionLine, visibilityWords } from './browserWords'
import { instanceAction } from './cardView'
import type { InstanceAction } from './cardView'

// The fields the header reads as strings: `summary: null`, say, would throw in clipText during render.
const INSTANCE_STRINGS = ['slug', 'title', 'kind', 'availability', 'summary', 'description', 'methods_text', 'status', 'visibility'] as const

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

export function isInstanceBody(data: unknown): data is GraphBrowserResponse {
  if (!isObject(data)) return false
  const instance = data.instance
  const releases = data.releases
  return isObject(instance)
    && INSTANCE_STRINGS.every((key) => typeof instance[key] === 'string')
    && Array.isArray(releases)
    && releases.every((release: unknown) => isObject(release) && isObject(release.dataset))
}

export interface VersionView { text: string; licence: ReleaseLine | null }   // a version line, then its release's licence line
export interface InstanceHeaderView {
  instance: GraphInstance                     // body.instance, for the explore area
  title: string; state: string; synthetic: boolean; visibility: string | null
  summary: string | null; description: string | null; methods: string | null
  citations: CitationLine[][]; organisms: string[]
  versions: VersionView[]; noRelease: boolean
  action: InstanceAction | null; network: boolean
}
export function instanceHeaderView(body: GraphBrowserResponse, vocabularies?: GraphVocabularies | null): InstanceHeaderView {
  const i = body.instance
  // Each release's scopes are read through stringItems, so F3's releaseEntryLines never reads a bad one,
  // and the result is still a GraphRelease for F3's functions.
  const releases = objectItems(body.releases).map((release) => ({ ...release, scopes: stringItems(release.scopes) }))
  // An entry whose ref or role is not text is skipped, neither shown nor counted, as any item of the wrong type in a
  // nested list (J13): its version line places both as text, and a role that is not text would throw during render.
  const selection = objectItems(i.selection).filter((entry) => typeof entry.release === 'string' && typeof entry.role === 'string')
  // In selection order, where `releases` comes in id order; a release the response lacks keeps its ref and
  // has no licence line. The full list renders beside the network, which names a subset of these (J26).
  const versions = selection.map((entry): VersionView => {
    const release = releases.find((candidate) => candidate.id === entry.release)
    return {
      text: versionLine(rowReleaseWords(release, entry.release), entry.role),
      licence: release === undefined ? null : releaseEntryLines(release).find((line) => line.term === RELEASE_TERMS.licence) ?? null,
    }
  })
  return {
    instance: i,
    title: clipText(i.title === '' ? i.slug : i.title),
    state: stateWords(i.kind, i.availability),
    synthetic: isSyntheticShown({ publications: objectItems(i.publications), releases, flags: [i.is_synthetic] }),
    visibility: visibilityWords(i.visibility),
    summary: i.summary === '' ? null : clipText(i.summary),
    // Whole, never clipped: the header shows the description and methods in full (J23).
    description: i.description === '' ? null : i.description,
    methods: i.methods_text === '' ? null : i.methods_text,
    citations: objectItems(i.publications).map(publicationLines),
    organisms: organismTaxids(i.taxa, i.primary_taxon).map((taxid) => taxonWords(taxid, vocabularies)),
    versions,
    // An empty selection can mean the caller may see none of it, so the page never says there is none (J24).
    noRelease: i.kind === 'typed' && selection.length === 0,
    action: instanceAction(i.kind, i.availability, i.external_url),
    // `portal_ppi` is live but pins nothing, and on other availabilities `browser=` would still read data.
    network: i.kind === 'typed' && i.availability === 'live',
  }
}
