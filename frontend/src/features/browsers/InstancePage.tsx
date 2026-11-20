import { useId, useRef, useState } from 'react'
import type { RefObject } from 'react'
import { Link, useLocation, useParams, useSearchParams } from 'react-router-dom'
import { useGraphBrowser } from '../../api/graph/browsers'
import { isGraphPathSegment } from '../../api/graph/params'
import { graphFeatureState, useGraphStatus } from '../../api/graph/status'
import { useGraphVocabularies } from '../../api/graph/vocabularies'
import { SyntheticBadge } from '../graph/badge/SyntheticBadge'
import { SYNTHETIC_EXPLAINED } from '../graph/badge/badgeWords'
import { own } from '../graph/network/graph'
import { presentationParams } from '../graph/network/presentation'
import { useArrivalScroll, useRetry, useStatusRecheck } from '../graph/pages/pageHooks'
import { PAGE_BUTTON_STYLE, PAGE_STYLE, PLAIN_LIST_STYLE, SECTION_HEADING_STYLE, STATE_LINE_STYLE } from '../graph/pages/pageStyle'
import { BROWSERS_TITLE } from '../graph/pages/pageWords'
import { routeErrorView } from '../graph/pages/routeState'
import { PageHeading, TypedRouteStatus } from '../graph/pages/TypedRouteStates'
import { endName, uniqueNames } from '../graph/panels/panelWords'
import { DETAIL_STYLE, LINK_STYLE, SOURCE_TEXT_STYLE, TERM_STYLE, clipText } from '../graph/panels/text'
import type { GraphEntityRef, GraphInstance, GraphNode, GraphVocabularies } from '../graph/types'
import { singleTaxon } from '../graph/words/taxa'
import { TypedNetworkWorkspace } from '../graph/workspace/TypedNetworkWorkspace'
import { useTypedNetworkWorkspace } from '../graph/workspace/useTypedNetworkWorkspace'
import {
  BACK_LINK, BROWSER_ERROR, BROWSER_NOT_AVAILABLE, BROWSER_TIMEOUT, HEADER_HEADINGS, LOADING_BROWSER, NO_RELEASE,
} from './browserWords'
import { searchString } from './galleryAddress'
import { instanceHeaderView, isInstanceBody } from './headerView'
import type { InstanceHeaderView } from './headerView'
import { InstanceActionLine } from './InstanceCard'
import { SEED_LIMIT_FALLBACK, galleryBackTo, seedsFromSearch, withSeeds } from './instanceAddress'
import { SeedSearch } from './SeedSearch'
import {
  CHOSEN_LABEL, NETWORK_HEADING, NO_SEED_PROMPT, REMOVE_ALL, SEEDS_ADDRESS_ERROR, SEEDS_REMOVAL, SEED_SECTION_HEADING, removeText,
} from './seedWords'

export function InstancePage() {
  // Keyed by slug, so the header, the seed search, the network, the selection and the scroll start afresh on
  // another instance, and nothing crosses between two.
  const { slug = '' } = useParams()
  return <InstanceBody key={slug} slug={slug} />
}

function InstanceBody({ slug }: { slug: string }) {
  useArrivalScroll()
  const headingRef = useRef<HTMLHeadingElement | null>(null)
  const location = useLocation()
  // B1's slug grammar (backend/browsers/rules.py:30-31): the API answers 404 to any other slug before any statement.
  const pathOk = isGraphPathSegment(slug) && slug.length <= 50 && /^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(slug)
  const browser = useGraphBrowser(slug, { enabled: pathOk })  // no keepPrevious (R-API:321)
  const vocabularies = useGraphVocabularies().data ?? null
  useStatusRecheck(browser)
  const body = browser.data
  // B1 finds the instance by the address's slug and echoes it, and the search and the network are scoped by the body's
  // slug: a body naming another slug, or none, would send them for another browser or unscoped, so it is no body here.
  const view = pathOk && isInstanceBody(body) && body.instance.slug === slug
    ? instanceHeaderView(body, vocabularies)   // the guard narrows `body` here
    : null
  const ok = view !== null
  const badBody = pathOk && body !== undefined && !ok
  const failure = browser.error === null || browser.error === undefined
    ? null
    : routeErrorView(browser.error, { other: BROWSER_ERROR, notFound: BROWSER_NOT_AVAILABLE, timeout: BROWSER_TIMEOUT })
  const retry = useRetry(browser, slug, (failure?.retry ?? false) || badBody)
  const line = !pathOk ? BROWSER_NOT_AVAILABLE : failure !== null ? failure.text : ok ? '' : badBody ? BROWSER_ERROR : LOADING_BROWSER
  const back = galleryBackTo(location.state)
  // A failed refetch keeps the cached header and says so above it, so a failure never reads as fresh. The
  // "Browsers" heading is transient while the query is pending, so a card click moves focus once, to the
  // title's heading, which mounts with the data; a pressed "Try again" that goes on success sends focus there.
  return (
    <div style={PAGE_STYLE}>
      {view === null && (
        <PageHeading headingRef={headingRef} ready={!pathOk || browser.status !== 'pending'}>{BROWSERS_TITLE}</PageHeading>
      )}
      <TypedRouteStatus text={line} onRetry={retry.offered ? retry.onRetry : undefined} busy={retry.busy} focusOnGone={headingRef} />
      {view === null ? (
        <p><Link to={back} style={LINK_STYLE}>{BACK_LINK}</Link></p>
      ) : (
        <>
          <InstanceHeader view={view} headingRef={headingRef} back={back} />
          <InstanceActionLine action={view.action} />
          {view.network && <InstanceExplore instance={view.instance} vocabularies={vocabularies} />}
        </>
      )}
    </div>
  )
}

interface InstanceHeaderProps {
  view: InstanceHeaderView
  headingRef: RefObject<HTMLHeadingElement | null>
  back: { pathname: string; search: string }
}

function InstanceHeader({ view, headingRef, back }: InstanceHeaderProps) {
  // Selectable, `pre-wrap` from the container, with the description and methods whole (J23). An address
  // reaches `href` only when it is http(s); each version line is followed by its release's licence in F3's
  // form, and the full release list renders only beside the network (J26).
  return (
    <div data-selectable style={SOURCE_TEXT_STYLE}>
      <PageHeading headingRef={headingRef}>{view.title}</PageHeading>
      <p style={STATE_LINE_STYLE}>{view.state}</p>
      {view.synthetic && <p><SyntheticBadge /> {SYNTHETIC_EXPLAINED}</p>}
      {view.visibility !== null && <p style={STATE_LINE_STYLE}>{view.visibility}</p>}
      {view.summary !== null && <p>{view.summary}</p>}
      {view.description !== null && (
        <>
          <h2 style={SECTION_HEADING_STYLE}>{HEADER_HEADINGS.description}</h2>
          <p>{view.description}</p>
        </>
      )}
      {view.methods !== null && (
        <>
          <h2 style={SECTION_HEADING_STYLE}>{HEADER_HEADINGS.methods}</h2>
          <p>{view.methods}</p>
        </>
      )}
      {view.citations.length > 0 && (
        <>
          <h2 style={SECTION_HEADING_STYLE}>{HEADER_HEADINGS.citation}</h2>
          <ul role="list" style={PLAIN_LIST_STYLE}>
            {view.citations.map((lines, index) => (
              <li key={index}>
                {lines.map((line, at) => (
                  <p key={at} style={DETAIL_STYLE}>
                    {line.href !== undefined
                      ? <a href={line.href} target="_blank" rel="noopener noreferrer" style={LINK_STYLE}>{line.text}</a>
                      : line.text}
                  </p>
                ))}
              </li>
            ))}
          </ul>
        </>
      )}
      {view.organisms.length > 0 && (
        <>
          <h2 style={SECTION_HEADING_STYLE}>{HEADER_HEADINGS.organisms}</h2>
          <ul role="list">
            {view.organisms.map((organism) => <li key={organism}>{organism}</li>)}
          </ul>
        </>
      )}
      {view.versions.length > 0 && (
        <>
          <h2 style={SECTION_HEADING_STYLE}>{HEADER_HEADINGS.releases}</h2>
          <ul role="list" style={PLAIN_LIST_STYLE}>
            {view.versions.map((version, index) => {
              const licence = version.licence
              return (
                <li key={index}>
                  <p>{version.text}</p>
                  {licence !== null && (
                    <dl>
                      <dt style={TERM_STYLE}>{licence.term}</dt>
                      <dd style={{ ...DETAIL_STYLE, ...SOURCE_TEXT_STYLE }}>
                        {licence.text}
                        {licence.href !== undefined && (
                          <>
                            {' '}
                            <a href={licence.href} target="_blank" rel="noopener noreferrer" style={LINK_STYLE}>{clipText(licence.href)}</a>
                          </>
                        )}
                      </dd>
                    </dl>
                  )}
                </li>
              )
            })}
          </ul>
        </>
      )}
      {view.versions.length === 0 && view.noRelease && (
        <>
          <h2 style={SECTION_HEADING_STYLE}>{HEADER_HEADINGS.releases}</h2>
          <p>{NO_RELEASE}</p>
        </>
      )}
      <p><Link to={back} style={LINK_STYLE}>{BACK_LINK}</Link></p>
    </div>
  )
}

function InstanceExplore({ instance, vocabularies }: { instance: GraphInstance; vocabularies: GraphVocabularies | null }) {
  // The cached `status`, read with no request and no timer, gives the seed limit and whether `resolve` and
  // `network` are on too (J14); a limit that is not a safe integer of at least 1 reads the fallback, since F1's
  // guard checks only `typed_graph` and `features`. Each write is one push that keeps the way back (J1, J10).
  const location = useLocation()
  const [searchParams, setSearchParams] = useSearchParams()
  const status = useGraphStatus({ enabled: false })
  const max = status.data?.limits?.max_seeds
  const maxSeeds = typeof max === 'number' && Number.isSafeInteger(max) && max >= 1 ? max : SEED_LIMIT_FALLBACK
  const explore = graphFeatureState(status, 'resolve') === 'on' && graphFeatureState(status, 'network') === 'on'
  const address = seedsFromSearch(searchParams, maxSeeds)
  const searchBox = useRef<HTMLInputElement | null>(null)
  const sectionHeading = useRef<HTMLHeadingElement | null>(null)
  const headingId = useId()
  const [named, setNamed] = useState<Readonly<Record<string, string>>>({})
  const write = (seeds: readonly GraphEntityRef[]) => setSearchParams(withSeeds(searchParams, seeds), { state: location.state })
  const add = (entity: GraphNode) => {
    if (address.seeds.includes(entity.id) || address.seeds.length >= maxSeeds) return
    const name = endName(entity, instance.presentation, vocabularies)
    setNamed((previous) => ({ ...previous, [entity.id]: name }))
    write([...address.seeds, entity.id])
  }
  const remove = (ref: GraphEntityRef) => { searchBox.current?.focus(); write(address.seeds.filter((seed) => seed !== ref)) }
  const clear = () => { searchBox.current?.focus(); write([]) }
  if (!explore) return null
  // Remove, "Remove all" and the removal link each take away their own control, so each moves focus first.
  return (
    <>
      <section aria-labelledby={headingId}>
        <h2 id={headingId} ref={sectionHeading} tabIndex={-1} style={SECTION_HEADING_STYLE}>{SEED_SECTION_HEADING}</h2>
        {address.invalid ? (
          <>
            <p>{SEEDS_ADDRESS_ERROR}</p>
            <p>
              <Link to={{ pathname: location.pathname, search: searchString(withSeeds(searchParams, [])) }} state={location.state}
                onClick={() => sectionHeading.current?.focus()} style={LINK_STYLE}>
                {SEEDS_REMOVAL}
              </Link>
            </p>
          </>
        ) : (
          <>
            <SeedSearch selection={{ browser: instance.slug }} taxon={singleTaxon(instance.taxa, instance.primary_taxon)}
              chosen={address.seeds} maxChosen={maxSeeds} onAdd={add} presentation={instance.presentation} vocabularies={vocabularies}
              inputRef={searchBox} />
            {address.seeds.length === 0 && <p style={STATE_LINE_STYLE}>{NO_SEED_PROMPT}</p>}
          </>
        )}
      </section>
      {!address.invalid && address.seeds.length > 0 && (
        <InstanceNetwork key={instance.slug} instance={instance} seeds={address.seeds} named={named} vocabularies={vocabularies}
          onRemove={remove} onClear={clear} />
      )}
    </>
  )
}

interface InstanceNetworkProps {
  instance: GraphInstance
  seeds: readonly GraphEntityRef[]
  named: Readonly<Record<string, string>>
  vocabularies: GraphVocabularies | null
  onRemove: (ref: GraphEntityRef) => void
  onClear: () => void
}

function InstanceNetwork({ instance, seeds, named, vocabularies, onRemove, onClear }: InstanceNetworkProps) {
  // Scoped by `browser=`, with the presentation's defaults and no `taxon` (J5, J28); a `not_found`, which the
  // network answers once the switch is off, rechecks `status`, so the gate can say so (J13).
  const params = { ...presentationParams(instance.presentation), seeds: [...seeds], browser: instance.slug }
  const workspace = useTypedNetworkWorkspace(params)
  useStatusRecheck(workspace.result)
  const headingId = useId()
  const labelId = useId()
  // A seed reads as its node's name, else the name the search gave it, else its ref, until its network answers.
  const nodes = workspace.graph === null ? [] : workspace.graph.nodes
  const names = uniqueNames(seeds.map((ref) => {
    const node = nodes.find((candidate) => candidate.id === ref)
    return { id: ref, name: node !== undefined ? endName(node, instance.presentation, vocabularies) : own(named, ref) ?? ref }
  }))
  return (
    <section aria-labelledby={headingId}>
      <h2 id={headingId} style={SECTION_HEADING_STYLE}>{NETWORK_HEADING}</h2>
      <p id={labelId} style={TERM_STYLE}>{CHOSEN_LABEL}</p>
      <ul role="list" aria-labelledby={labelId} style={PLAIN_LIST_STYLE}>
        {seeds.map((ref, index) => (
          <li key={ref}>
            {names[index]}{' '}
            <button type="button" onClick={() => onRemove(ref)} style={PAGE_BUTTON_STYLE}>{removeText(names[index])}</button>
          </li>
        ))}
      </ul>
      <button type="button" onClick={onClear} style={PAGE_BUTTON_STYLE}>{REMOVE_ALL}</button>
      <TypedNetworkWorkspace workspace={workspace} presentation={instance.presentation} vocabularies={vocabularies} headingLevel={3} />
    </section>
  )
}
