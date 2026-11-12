import type { TypedGraph } from './graph'
import { noticeLines } from './notice'

interface TypedNetworkNoticeProps {
  graph: TypedGraph | null
  showNonSupporting?: boolean
}

/**
 * The live region renders before there is anything to say, since a region that appears
 * together with its first message is not announced; a page mounts it before the first
 * response. Its lines are distinct sentences, so each is its own key.
 */
export function TypedNetworkNotice({ graph, showNonSupporting }: TypedNetworkNoticeProps) {
  const lines = noticeLines(graph, { showNonSupporting })
  return (
    <div role="status" aria-live="polite">
      {lines.map((line) => (
        <p key={line}>{line}</p>
      ))}
    </div>
  )
}
