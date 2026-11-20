import { useId, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { Link, useLocation, useSearchParams } from 'react-router-dom'
import type { NavigateOptions } from 'react-router-dom'
import { useGraphBrowsers } from '../../api/graph/browsers'
import { useGraphVocabularies } from '../../api/graph/vocabularies'
import { useArrivalScroll, useRetry, useStatusRecheck } from '../graph/pages/pageHooks'
import {
  CARD_LIST_STYLE, FIELD_STYLE, FORM_ROW_STYLE, INPUT_STYLE, PAGE_BUTTON_STYLE, PAGE_STYLE, STATE_LINE_STYLE,
} from '../graph/pages/pageStyle'
import { BROWSERS_TITLE } from '../graph/pages/pageWords'
import { PageHeading, TypedRouteStatus } from '../graph/pages/TypedRouteStates'
import { LINK_STYLE } from '../graph/panels/text'
import {
  ANY_OPTION, CLEAR_FILTERS, FILTERS_NAME, FILTER_LABELS, LOADING_BROWSERS, SEARCH_BUTTON, SEARCH_CLEAR, SEARCH_FORM_NAME, SEARCH_LABEL,
  removalText,
} from './browserWords'
import { galleryFilterOptions } from './filterOptions'
import type { FilterOptions } from './filterOptions'
import {
  GALLERY_FILTER_NAMES, GALLERY_PARAM_NAMES, Q_MAX_CHARS, filterWrite, galleryAddress, isSearched, searchString, withGalleryParam,
  withoutGalleryParams,
} from './galleryAddress'
import type { GalleryParamName } from './galleryAddress'
import { galleryView } from './galleryView'
import { InstanceCard } from './InstanceCard'
import { galleryLinkState } from './instanceAddress'

export function GalleryPage() {
  // An arrival at the gallery scrolls to the top, Back included; a search or a filter change keeps the page
  // mounted and does not (J30). No list request goes out for an invalid address, and the unfiltered list,
  // the filter options' source, shares its key with the list when nothing is set (J16, J18).
  useArrivalScroll()
  const headingRef = useRef<HTMLHeadingElement | null>(null)
  const location = useLocation()
  const [searchParams, setSearchParams] = useSearchParams()
  const address = galleryAddress(searchParams)
  const list = useGraphBrowsers(address.params, { keepPrevious: true, enabled: address.invalid.length === 0 })
  const unfiltered = useGraphBrowsers({})
  const vocabularies = useGraphVocabularies().data ?? null
  useStatusRecheck(list)
  const view = galleryView(address, list)
  const retry = useRetry(list, JSON.stringify(address.params), view.retry)
  const options = galleryFilterOptions(unfiltered.data, vocabularies, address.shown)
  const write = (next: URLSearchParams, how?: NavigateOptions) => setSearchParams(next, how)
  return (
    <div style={PAGE_STYLE}>
      <PageHeading headingRef={headingRef}>{BROWSERS_TITLE}</PageHeading>
      <GallerySearch urlQ={address.shown.q ?? null} search={searchParams} onWrite={write} />
      <GalleryFilters options={options} shown={address.shown} search={searchParams} locationState={location.state} onWrite={write} />
      <TypedRouteStatus text={view.line} onRetry={retry.offered ? retry.onRetry : undefined} busy={retry.busy} />
      {view.removal !== null && (
        <p>
          <Link
            to={{
              pathname: '/browsers',
              search: searchString(withoutGalleryParams(searchParams, view.removal.length === 0 ? GALLERY_PARAM_NAMES : view.removal)),
            }}
            // The link removes itself, so focus moves to the heading first.
            onClick={() => headingRef.current?.focus()}
            style={LINK_STYLE}
          >
            {removalText(view.removal)}
          </Link>
        </p>
      )}
      {view.loading && <p style={STATE_LINE_STYLE}>{LOADING_BROWSERS}</p>}
      {view.instances !== null && view.instances.length > 0 && (
        <ul role="list" aria-busy={view.loading ? 'true' : undefined} style={CARD_LIST_STYLE}>
          {view.instances.map((instance) => (
            <li key={instance.slug}>
              <InstanceCard instance={instance} vocabularies={vocabularies} linkState={galleryLinkState(location.search)} />
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

interface GallerySearchProps {
  urlQ: string | null
  search: URLSearchParams
  onWrite: (next: URLSearchParams) => void
}

function GallerySearch({ urlQ, search, onWrite }: GallerySearchProps) {
  const id = useId()
  const inputRef = useRef<HTMLInputElement | null>(null)
  const [draft, setDraft] = useState<{ source: string | null; text: string }>({ source: urlQ, text: urlQ ?? '' })
  // The box follows Back and Forward: a draft typed for another address gives way to this one's `q`, through a
  // guarded update during render, since a state set in an effect fails lint (J17).
  const text = draft.source === urlQ ? draft.text : (urlQ ?? '')
  if (draft.source !== urlQ) setDraft({ source: urlQ, text: urlQ ?? '' })
  // Typing sends nothing: one request per submit, and an unchanged value writes no second history entry (J44).
  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const next = text.trim()
    if (next === '') {
      if (urlQ !== null) onWrite(withGalleryParam(search, 'q', ''))
      return
    }
    // A repeated `q` that the box shows unchanged is written once, when the page can use that one value (J16).
    const written = withGalleryParam(search, 'q', next)
    if (next !== urlQ || (search.getAll('q').length > 1 && isSearched(galleryAddress(written)))) onWrite(written)
  }
  const clear = () => {
    inputRef.current?.focus()
    setDraft({ source: urlQ, text: '' })
    if (urlQ !== null) onWrite(withGalleryParam(search, 'q', ''))
  }
  return (
    <form role="search" aria-label={SEARCH_FORM_NAME} onSubmit={submit} style={FORM_ROW_STYLE}>
      <div style={FIELD_STYLE}>
        <label htmlFor={id}>{SEARCH_LABEL}</label>
        <input id={id} ref={inputRef} type="search" maxLength={Q_MAX_CHARS} value={text}
          onChange={(event) => setDraft({ source: urlQ, text: event.target.value })} style={INPUT_STYLE} />
      </div>
      <button type="submit" style={PAGE_BUTTON_STYLE}>{SEARCH_BUTTON}</button>
      {(text !== '' || urlQ !== null) && (
        <button type="button" onClick={clear} style={PAGE_BUTTON_STYLE}>{SEARCH_CLEAR}</button>
      )}
    </form>
  )
}

interface GalleryFiltersProps {
  options: FilterOptions
  shown: Partial<Record<GalleryParamName, string>>
  search: URLSearchParams
  locationState: unknown
  onWrite: (next: URLSearchParams, how?: NavigateOptions) => void
}

function GalleryFilters({ options, shown, search, locationState, onWrite }: GalleryFiltersProps) {
  // Every select reaches the address through this one handler; consecutive changes of one select replace one
  // history entry (J16, J18).
  const baseId = useId()
  const firstRef = useRef<HTMLSelectElement | null>(null)
  const filtered = GALLERY_FILTER_NAMES.some((name) => shown[name] !== undefined)
  return (
    <div role="group" aria-label={FILTERS_NAME} style={FORM_ROW_STYLE}>
      {GALLERY_FILTER_NAMES.map((name, index) => (
        <div key={name} style={FIELD_STYLE}>
          <label htmlFor={`${baseId}${name}`}>{FILTER_LABELS[name]}</label>
          <select
            id={`${baseId}${name}`}
            ref={index === 0 ? firstRef : undefined}
            value={shown[name] ?? ''}
            onChange={(event) => onWrite(withGalleryParam(search, name, event.target.value), filterWrite(locationState, name))}
            style={INPUT_STYLE}
          >
            <option value="">{ANY_OPTION[name]}</option>
            {options[name].map((option) => (
              <option key={option.value} value={option.value}>{option.text}</option>
            ))}
          </select>
        </div>
      ))}
      {filtered && (
        <button
          type="button"
          style={PAGE_BUTTON_STYLE}
          onClick={() => {
            // The button removes itself, so focus moves to the first select first.
            firstRef.current?.focus()
            onWrite(withoutGalleryParams(search, GALLERY_FILTER_NAMES))
          }}
        >
          {CLEAR_FILTERS}
        </button>
      )}
    </div>
  )
}
