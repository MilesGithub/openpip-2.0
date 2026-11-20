import type { GraphPublication } from '../types'
import { clipText, isHttpUrl } from '../panels/text'
import { isolateText } from './isolateText'

export interface CitationLine { text: string; href?: string }   // href only for isHttpUrl
export const NO_TITLE = 'title not recorded'
export const SYNTHETIC_SUFFIX = ' (synthetic)'

export function paperTitle(publication: GraphPublication): string {
  return `${isolateText(clipText(publication.title || NO_TITLE))}${publication.is_synthetic ? SYNTHETIC_SUFFIX : ''}`
}

export function publicationLines(publication: GraphPublication): CitationLine[] {
  // F3's evidence-row lines without the role (panels/evidence.ts), restated so that F3 stays unedited; a
  // test compares the two once the title's isolates are removed (J25). Identifiers stay text (J9).
  const lines: CitationLine[] = [{ text: paperTitle(publication) }]
  const about = [publication.authors, publication.journal, publication.year === null ? '' : String(publication.year)]
    .filter((part) => typeof part === 'string' && part !== '')
    .map((part) => clipText(part))
  if (about.length > 0) lines.push({ text: about.join(', ') })
  const identifiers: string[] = []
  if (publication.pmid !== null) identifiers.push(`PMID ${clipText(publication.pmid)}`)
  if (publication.doi !== null) identifiers.push(`DOI ${clipText(publication.doi)}`)
  if (publication.pmcid !== null) identifiers.push(`PMCID ${clipText(publication.pmcid)}`)
  if (publication.imex_id !== null) identifiers.push(`IMEx ${clipText(publication.imex_id)}`)
  if (publication.local_key !== null) identifiers.push(`local key ${clipText(publication.local_key)}`)
  if (identifiers.length > 0) lines.push({ text: identifiers.join('; ') })
  const url = publication.url
  if (isHttpUrl(url)) lines.push({ text: clipText(url), href: url })
  else if (typeof url === 'string' && url !== '') lines.push({ text: clipText(url) })
  return lines
}

export function firstCitedText(publication: GraphPublication): string {
  return publication.year === null ? paperTitle(publication) : `${paperTitle(publication)}, ${publication.year}`
}
