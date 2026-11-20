import { typeLabel } from '../graph/network/words'
import { isSyntheticShown } from '../graph/panels/releases'
import { clipText, isHttpUrl } from '../graph/panels/text'
import type { GraphInstanceItem, GraphVocabularies } from '../graph/types'
import { firstCitedText } from '../graph/words/citation'
import { organismTaxids, taxonWords } from '../graph/words/taxa'
import {
  CARD_TERMS, EXTERNAL_ACTION, PORTAL_SEARCH_ACTION, objectItems, stateWords, stringItems, tagWords, visibilityWords,
} from './browserWords'
import { instanceHref } from './instanceAddress'

export type InstanceAction =
  | { kind: 'search'; text: string; to: '/search' }
  | { kind: 'external'; lead: string; text: string; href: string }   // isHttpUrl only
  | { kind: 'externalText'; lead: string; text: string }            // any other non-empty address, as text
export interface CardFact { term: string; text: string }
export interface InstanceCardView {
  href: string; title: string; state: string; synthetic: boolean; visibility: string | null
  summary: string | null; facts: CardFact[]; action: InstanceAction | null
}
export function instanceAction(kind: string, availability: string, externalUrl: string | null): InstanceAction | null {
  // A live typed instance's title link is its action; planned, retired and metadata-only entries have none.
  if (availability === 'live' && kind === 'portal_ppi') return { kind: 'search', text: PORTAL_SEARCH_ACTION, to: '/search' }
  if (availability === 'external' && kind === 'external') {
    // Shown as stored and never fetched. React rewrites only the `javascript:` forms it sees, so only an
    // http(s) address reaches `href`, and any other shows as text.
    if (isHttpUrl(externalUrl)) return { kind: 'external', lead: EXTERNAL_ACTION, text: clipText(externalUrl), href: externalUrl }
    if (typeof externalUrl === 'string' && externalUrl !== '') return { kind: 'externalText', lead: EXTERNAL_ACTION, text: clipText(externalUrl) }
  }
  return null
}

export function instanceCardView(item: GraphInstanceItem, vocabularies?: GraphVocabularies | null): InstanceCardView {
  const publications = objectItems(item.publications)
  const facts: CardFact[] = []
  if (publications.length > 0) facts.push({ term: CARD_TERMS.cited, text: firstCitedText(publications[0]) })
  const types = stringItems(item.relationship_types).map((slug) => clipText(typeLabel(slug, vocabularies)))
  if (types.length > 0) facts.push({ term: CARD_TERMS.relationshipTypes, text: types.join(', ') })
  const organisms = organismTaxids(item.taxa, item.primary_taxon).map((taxid) => taxonWords(taxid, vocabularies))
  if (organisms.length > 0) facts.push({ term: CARD_TERMS.organisms, text: organisms.join(', ') })
  const tags = stringItems(item.tags).map((tag) => clipText(tagWords(tag)))
  if (tags.length > 0) facts.push({ term: CARD_TERMS.tags, text: tags.join(', ') })
  return {
    href: instanceHref(item.slug),
    // A title of '' reads as the slug, so the card's link always has a name.
    title: clipText(item.title === '' ? item.slug : item.title),
    state: stateWords(item.kind, item.availability),
    // Never the flag alone: it counts only the releases the caller may see, while papers go to every caller.
    // A card has no releases (J21).
    synthetic: isSyntheticShown({ publications, flags: [item.is_synthetic] }),
    visibility: visibilityWords(item.visibility),
    summary: item.summary === '' ? null : clipText(item.summary),
    facts,
    action: instanceAction(item.kind, item.availability, item.external_url),
  }
}
