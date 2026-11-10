import * as browsers from './browsers'
import * as entities from './entities'
import * as network from './network'
import type { RecordedEntry } from './recorded'
import * as relationships from './relationships'
import * as resolve from './resolve'
import * as status from './status'
import * as vocabularies from './vocabularies'

export const recordedEntries: readonly RecordedEntry[] = [
  ...Object.values(status),
  ...Object.values(vocabularies),
  ...Object.values(resolve),
  ...Object.values(entities),
  ...Object.values(network),
  ...Object.values(relationships),
  ...Object.values(browsers),
]
