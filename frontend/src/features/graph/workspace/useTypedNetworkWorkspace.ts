import type { GraphNetworkParams } from '../../../api/graph/params'
import { useTypedNetwork } from '../network/useTypedNetwork'
import type { TypedNetworkResult } from '../network/useTypedNetwork'
import { defaultShowNonSupporting } from '../network/visibility'
import { useTypedSelection } from '../panels/selection'
import type { PanelSelection } from '../panels/selection'

export interface TypedNetworkWorkspaceState extends TypedNetworkResult {
  show: boolean                                         // options.showNonSupporting when given, else defaultShowNonSupporting(graph)
  selected: PanelSelection | null
  select: (selection: PanelSelection | null) => void
}
/** X1's options (Q33), each optional, so a call with `params` alone, the instance page's, works as before. */
export interface TypedNetworkWorkspaceOptions {
  initial?: PanelSelection | null   // the selection to restore, read once by F3's hook (panels/selection.ts:63); a later one goes through select
  showNonSupporting?: boolean       // controlled: given, it is `show`, in place of defaultShowNonSupporting(graph)
}
export function useTypedNetworkWorkspace(
  params: GraphNetworkParams | null,
  options: TypedNetworkWorkspaceOptions = {},
): TypedNetworkWorkspaceState {
  // F3's composition unchanged: one `show` and one `select` serve the canvas, the relationship rows and the panels.
  // X1's explorer passes its address's selection as `initial` and its tested-absent toggle as `showNonSupporting`.
  const network = useTypedNetwork(params)
  const show = options.showNonSupporting ?? defaultShowNonSupporting(network.graph)
  const { selected, select } = useTypedSelection(network.graph, { showNonSupporting: show, initial: options.initial })
  return { ...network, show, selected, select }
}
