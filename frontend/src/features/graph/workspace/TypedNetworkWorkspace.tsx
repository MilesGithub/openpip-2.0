import { TypedLegend } from '../legend/TypedLegend'
import { TypedNetwork } from '../network/TypedNetwork'
import { TypedNetworkNotice } from '../network/TypedNetworkNotice'
import { ReleaseList } from '../panels/ReleaseList'
import { TypedSelectionPanel } from '../panels/TypedSelectionPanel'
import { SUBHEADING_STYLE } from '../panels/text'
import type { HeadingLevel } from '../panels/text'
import { TypedEdgesTable } from '../tables/TypedEdgesTable'
import type { GraphPresentation, GraphVocabularies } from '../types'
import type { TypedNetworkWorkspaceState } from './useTypedNetworkWorkspace'
import {
  MAIN_COLUMN_STYLE, PANEL_COLUMN_STYLE, RELEASES_IN_NETWORK, TABLE_REGION_LABEL, TABLE_REGION_STYLE, WORKSPACE_ROW_STYLE,
} from './workspaceWords'

export interface TypedNetworkWorkspaceProps {
  workspace: TypedNetworkWorkspaceState
  presentation?: GraphPresentation | null
  vocabularies?: GraphVocabularies | null
  headingLevel?: HeadingLevel            // the panel's and the release list's heading level; default 3
  maxExpandedSeeds?: number | null       // status.limits.max_expanded_seeds, for the notice's derived-seed line (X1); the instance page passes none
}
export function TypedNetworkWorkspace(props: TypedNetworkWorkspaceProps) {
  const { workspace, presentation, vocabularies, headingLevel = 3, maxExpandedSeeds } = props
  const { graph, state, error, show, selected, select } = workspace
  const Heading = `h${headingLevel}` as const
  const releases = graph === null ? [] : graph.releases
  // The notice mounts with the workspace, before the first response, so its first message is announced. The
  // panel follows the canvas in DOM order, so a wrapped panel stays beside it on a narrow screen, and mounts
  // only while something is selected, so Close and Escape unmount it (J28, J29). The relationship rows' wrapper
  // scrolls sideways, takes keyboard focus and is named here, since their caption has no id to point at.
  // The drawn network's releases are the page's one full release list, with licence, attribution and note of
  // changes (J26).
  return (
    <>
      <div style={WORKSPACE_ROW_STYLE}>
        <div style={MAIN_COLUMN_STYLE}>
          <TypedNetworkNotice graph={graph} showNonSupporting={show} maxExpandedSeeds={maxExpandedSeeds} />
          <TypedNetwork graph={graph} state={state} error={error} vocabularies={vocabularies} presentation={presentation}
            selected={selected} onSelect={select} showNonSupporting={show} />
        </div>
        {selected !== null && (
          <div style={PANEL_COLUMN_STYLE}>
            <TypedSelectionPanel selection={selected} graph={graph} vocabularies={vocabularies} presentation={presentation}
              onSelect={select} onClose={() => select(null)} headingLevel={headingLevel} />
          </div>
        )}
      </div>
      <div role="region" aria-label={TABLE_REGION_LABEL} tabIndex={0} style={TABLE_REGION_STYLE}>
        <TypedEdgesTable graph={graph} vocabularies={vocabularies} presentation={presentation}
          selected={selected} onSelect={select} showNonSupporting={show} />
      </div>
      <TypedLegend graph={graph} vocabularies={vocabularies} showNonSupporting={show} />
      {releases.length > 0 && (
        <>
          <Heading style={SUBHEADING_STYLE}>{RELEASES_IN_NETWORK}</Heading>
          <ReleaseList releases={releases} />
        </>
      )}
    </>
  )
}
