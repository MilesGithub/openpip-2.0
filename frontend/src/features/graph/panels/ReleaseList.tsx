import { Fragment } from 'react'
import type { GraphRelease } from '../types'
import { releaseEntryLines } from './releases'
import { DETAIL_STYLE, LINK_STYLE, SOURCE_TEXT_STYLE, TERM_STYLE, clipText } from './text'

export interface ReleaseListProps { releases: readonly GraphRelease[] }

/**
 * Licence, attribution and note of changes are shown together, as CC BY and CC BY-SA require. The
 * lines carry the style themselves, since a page may render the list outside a panel.
 */
export function ReleaseList({ releases }: ReleaseListProps) {
  if (releases.length === 0) return null
  return (
    <ul role="list" style={{ listStyle: 'none', margin: 0, padding: 0, width: '100%' }}>
      {releases.map((release) => (
        <li key={release.id}>
          <dl data-selectable>
            {releaseEntryLines(release).map((line, index) => (
              <Fragment key={index}>
                <dt style={TERM_STYLE}>{line.term}</dt>
                <dd style={{ ...DETAIL_STYLE, ...SOURCE_TEXT_STYLE }}>
                  {line.text}
                  {line.href !== undefined && (
                    <>
                      {' '}
                      <a href={line.href} target="_blank" rel="noopener noreferrer" style={LINK_STYLE}>{clipText(line.href)}</a>
                    </>
                  )}
                </dd>
              </Fragment>
            ))}
          </dl>
        </li>
      ))}
    </ul>
  )
}
