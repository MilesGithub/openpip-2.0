import type { GraphNetworkParams } from '../../../api/graph/params'
import { useTypedNetwork } from '../network/useTypedNetwork'
import type { TypedNetworkResult } from '../network/useTypedNetwork'
import { defaultShowNonSupporting } from '../network/visibility'
import { useTypedSelection } from '../panels/selection'
import type { PanelSelection } from '../panels/selection'

export interface TypedNetworkWorkspaceState extends TypedNetworkResult {
  show: boolean                                         // defaultShowNonSupporting(graph)
  selected: PanelSelection | null
  select: (selection: PanelSelection | null) => void
}
export function useTypedNetworkWorkspace(params: GraphNetworkParams | null): TypedNetworkWorkspaceState {
  // F3's composition unchanged: one `show` and one `select` serve the canvas, the relationship rows and the panels.
  // X1 adds its own options in its own PR; F4 adds none that no caller passes.
  const network = useTypedNetwork(params)
  const show = defaultShowNonSupporting(network.graph)
  const { selected, select } = useTypedSelection(network.graph, { showNonSupporting: show })
  return { ...network, show, selected, select }
}
