/** Who the response was recorded for. `off` is the typed graph with its switch off. */
export type RecordedCaller = 'staff' | 'visitor' | 'off'

/** One recorded request: what was asked, of whom, and what the API answered. */
export interface RecordedEntry<TBody = unknown> {
  name: string
  caller: RecordedCaller
  /** The path as the API writes it, with first-load refs. */
  path: string
  /** Name and value pairs, lists comma-joined, as the client sends them. */
  query: [string, string][]
  status: number
  body: TBody
}

/** The demonstration's refs on a first load: this fixture's own convention, never a rule about real data. */
export const demoRefs = {
  g1: 'entity:1', g2: 'entity:2', g3: 'entity:3', g4: 'entity:4', g5: 'entity:5', g6: 'entity:6',
  tf1: 'entity:7', tf2: 'entity:8', tf3: 'entity:9', p1: 'entity:10', p1Isoform: 'entity:11', p2: 'entity:12', p3: 'entity:13',
  v1: 'release:1', v2: 'release:2',
  encodes: 'relationship:1', isoformOf: 'relationship:2', symmetric: 'relationship:3', testedFor: 'relationship:4',
  reverse: 'relationship:5', forward: 'relationship:6', testedAbsent: 'relationship:7', unknownEffect: 'relationship:8',
  mixed: 'relationship:9', selfLoop: 'relationship:10', oneLabel: 'relationship:11', notStated: 'relationship:12',
  prediction: 'relationship:13', twoPapers: 'relationship:14',
} as const
