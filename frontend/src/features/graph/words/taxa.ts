import { arrayOf, clipText } from '../panels/text'
import type { GraphVocabularies } from '../types'

function taxonEntry(taxid: number, vocabularies?: GraphVocabularies | null) {
  return arrayOf(vocabularies?.taxa).find((entry) => entry.taxid === taxid)
}

export function taxonWords(taxid: number, vocabularies?: GraphVocabularies | null): string {
  const entry = taxonEntry(taxid, vocabularies)
  return entry === undefined ? `taxid ${taxid}` : `${clipText(entry.scientific_name)} (taxid ${taxid})`
}

export function taxonName(taxid: number, vocabularies?: GraphVocabularies | null): string {
  const entry = taxonEntry(taxid, vocabularies)
  return entry === undefined ? `taxid ${taxid}` : clipText(entry.scientific_name)
}

function isTaxid(value: unknown): value is number {
  return typeof value === 'number' && Number.isSafeInteger(value)
}

export function organismTaxids(taxa: readonly number[] | null | undefined, primary: number | null | undefined): number[] {
  // `taxa` holds numbers, which neither objectItems nor stringItems keeps, so an item that is not a taxid is
  // skipped here, never shown or counted. The primary taxon comes first and can lie outside `taxa`.
  const taxids: number[] = []
  if (isTaxid(primary)) taxids.push(primary)
  const items: readonly unknown[] = Array.isArray(taxa) ? taxa : []
  for (const item of items) {
    if (isTaxid(item) && !taxids.includes(item)) taxids.push(item)
  }
  return taxids
}

export function singleTaxon(taxa: readonly number[] | null | undefined, primary: number | null | undefined): number | undefined {
  // A cross-taxon instance sends no `taxon` (J1).
  const taxids = organismTaxids(taxa, primary)
  return taxids.length === 1 ? taxids[0] : undefined
}
