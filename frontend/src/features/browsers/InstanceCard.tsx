import { Fragment } from 'react'
import { Link } from 'react-router-dom'
import { SyntheticBadge } from '../graph/badge/SyntheticBadge'
import { CARD_STYLE, CARD_TITLE_STYLE, STATE_LINE_STYLE } from '../graph/pages/pageStyle'
import { DETAIL_STYLE, LINK_STYLE, SOURCE_TEXT_STYLE, TERM_STYLE } from '../graph/panels/text'
import type { HeadingLevel } from '../graph/panels/text'
import type { GraphInstanceItem, GraphVocabularies } from '../graph/types'
import { instanceCardView } from './cardView'
import type { InstanceAction } from './cardView'

export interface InstanceCardProps {
  instance: GraphInstanceItem
  vocabularies?: GraphVocabularies | null
  headingLevel?: HeadingLevel     // default 2
  linkState?: unknown             // router state for the title link (J10)
}
export function InstanceCard(props: InstanceCardProps) {
  const { instance, vocabularies, headingLevel = 2, linkState } = props
  // The title is the card's one link to the instance, never the whole card, so the outbound link is never
  // nested inside another (J20).
  const view = instanceCardView(instance, vocabularies)
  const Heading = `h${headingLevel}` as const
  return (
    <div data-selectable style={CARD_STYLE}>
      <Heading style={CARD_TITLE_STYLE}>
        <Link to={view.href} state={linkState} style={LINK_STYLE}>{view.title}</Link>
      </Heading>
      <p style={STATE_LINE_STYLE}>{view.state}</p>
      {view.synthetic && <p><SyntheticBadge /></p>}
      {view.visibility !== null && <p style={STATE_LINE_STYLE}>{view.visibility}</p>}
      {view.summary !== null && <p style={SOURCE_TEXT_STYLE}>{view.summary}</p>}
      {view.facts.length > 0 && (
        <dl>
          {view.facts.map((fact) => (
            <Fragment key={fact.term}>
              <dt style={TERM_STYLE}>{fact.term}</dt>
              <dd style={{ ...DETAIL_STYLE, ...SOURCE_TEXT_STYLE }}>{fact.text}</dd>
            </Fragment>
          ))}
        </dl>
      )}
      <InstanceActionLine action={view.action} />
    </div>
  )
}

export function InstanceActionLine(props: { action: InstanceAction | null }) {
  const { action } = props
  if (action === null) return null
  // A router link, so the router adds the basename; an outbound address reaches `href` only when cardView
  // found it http(s), and any other shows as text. Both address lines break anywhere: an address can be one run
  // of characters wider than the page.
  if (action.kind === 'search') {
    return <p><Link to="/search" style={LINK_STYLE}>{action.text}</Link></p>
  }
  if (action.kind === 'external') {
    return (
      <p style={SOURCE_TEXT_STYLE}>
        {action.lead}{' '}
        <a href={action.href} target="_blank" rel="noopener noreferrer" style={LINK_STYLE}>{action.text}</a>
      </p>
    )
  }
  return <p style={SOURCE_TEXT_STYLE}>{action.lead} {action.text}</p>
}
