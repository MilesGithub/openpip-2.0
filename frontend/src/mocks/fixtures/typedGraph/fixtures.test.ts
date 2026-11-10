import { describe, expect, it } from 'vitest'
import type {
  GraphEdge,
  GraphEntityResponse,
  GraphEvidenceResponse,
  GraphEvidenceRow,
  GraphNetworkResponse,
  GraphPrimaryIdentifier,
  GraphRelationshipResponse,
  GraphResolveResponse,
  GraphResolveResult,
} from '../../../features/graph/types'
import { recorded } from '../../../test/graphHooks'
import { recordedEntries } from './index'
import { demoRefs } from './recorded'

const NAMES = [
  'status-on',
  'status-off',
  'vocabularies',
  'resolve-tf1-shared',
  'resolve-alpha-variant',
  'resolve-unmatched',
  'resolve-unknown-parameter',
  'resolve-browser-and-releases',
  'entity-tf1',
  'entity-p1',
  'network-tf1',
  'network-tf1-outcome-all',
  'network-tf1-v1',
  'network-tf1-both-releases',
  'network-all-edges',
  'network-tf1-browser',
  'network-tf1-visitor',
  'off-not-found',
  'relationship-mixed',
  'relationship-tested-absent',
  'relationship-effect-v1',
  'relationship-mixed-visitor',
  'evidence-mixed',
  'evidence-scores',
  'evidence-physical',
  'evidence-tested-for',
  'evidence-mixed-page-1',
  'evidence-mixed-page-2',
  'browsers',
  'browsers-visitor',
  'browser-typed',
  'browser-portal-ppi',
]

const edge = (entry: string, ref: string): GraphEdge =>
  recorded<GraphNetworkResponse>(entry).body.edges.find((candidate) => candidate.id === ref)
const relationship = (entry: string) => recorded<GraphRelationshipResponse>(entry).body
const entity = (entry: string) => recorded<GraphEntityResponse>(entry).body
const evidence = (entry: string) => recorded<GraphEvidenceResponse>(entry).body.evidence
const recordKeys = (entry: string) => evidence(entry).map((row) => row.record?.key)
const evidenceRow = (entry: string, key: string): GraphEvidenceRow =>
  evidence(entry).find((row) => row.record?.key === key)
const term = (entry: string, text: string): GraphResolveResult =>
  recorded<GraphResolveResponse>(entry).body.results.find((result) => result.term === text)
const candidates = (result: GraphResolveResult) => result.candidates.map((candidate) => candidate.entity.id)

function walk(value: unknown, visit: (object: Record<string, unknown>) => void): void {
  if (Array.isArray(value)) {
    for (const item of value) walk(item, visit)
  } else if (typeof value === 'object' && value !== null) {
    const object = value as Record<string, unknown>
    visit(object)
    for (const item of Object.values(object)) walk(item, visit)
  }
}

describe('the recorded entries', () => {
  it('are named, and each is one request', () => {
    const names = recordedEntries.map((entry) => entry.name)
    expect(names.filter((name, index) => names.indexOf(name) !== index)).toEqual([])
    expect(NAMES.filter((name) => !names.includes(name))).toEqual([])
    const requests = recordedEntries.map((entry) => JSON.stringify([entry.caller, entry.path, entry.query]))
    expect(recordedEntries.filter((_, index) => requests.indexOf(requests[index]) !== index).map((entry) => entry.name)).toEqual([])
  })

  it('can never be mistaken for a finding', () => {
    const bad: string[] = []
    const seen = { nodes: 0, publications: 0, releases: 0 }
    for (const entry of recordedEntries) {
      if (entry.name === 'vocabularies') continue
      walk(entry.body, (object) => {
        if ('primary_identifier' in object) {
          seen.nodes += 1
          const identifier = object.primary_identifier as GraphPrimaryIdentifier | null
          if (typeof object.label !== 'string' || !object.label.startsWith('SYN-')) {
            bad.push(`${entry.name}: label ${String(object.label)}`)
          }
          if (identifier === null || !['synthetic_gene', 'synthetic_protein'].includes(identifier.namespace)) {
            bad.push(`${entry.name}: namespace of ${String(object.label)}`)
          }
        }
        if ('pmid' in object) {
          seen.publications += 1
          if (object.pmid !== null || object.doi !== null || object.is_synthetic !== true) {
            bad.push(`${entry.name}: publication ${JSON.stringify(object)}`)
          }
        }
        if ('scopes' in object) {
          seen.releases += 1
          if (object.is_synthetic !== true) bad.push(`${entry.name}: release ${String(object.id)}`)
        }
      })
    }
    expect(bad).toEqual([])
    expect(seen.nodes).toBeGreaterThan(0)
    expect(seen.publications).toBeGreaterThan(0)
    expect(seen.releases).toBeGreaterThan(0)
  })

  it('hold no reported row', () => {
    const reported = /syn-x\d|SYN-g7|not_a_relationship_type|not_an_entity_type/
    expect(recordedEntries.filter((entry) => reported.test(JSON.stringify(entry.body))).map((entry) => entry.name)).toEqual([])
  })

  it('stay under a megabyte', () => {
    const sizes = recordedEntries.map((entry) => `${entry.name}=${JSON.stringify(entry.body, null, 2).length}`)
    expect(JSON.stringify(recordedEntries, null, 2).length, sizes.join(' ')).toBeLessThan(1_000_000)
  })
})

interface ReadmeCase {
  row: string
  entries: string[]
  holds: () => boolean
}

const mixedEdgeHolds = () => relationship('relationship-mixed').edge.effect === 'mixed'
const twoPublicationsHold = () => relationship('relationship-mixed').edge.distinct.publications === 2

const README_CASES: ReadmeCase[] = [
  {
    row: 'syn-01',
    entries: ['network-tf1', 'network-tf1-v1', 'network-tf1-both-releases', 'resolve-tf1-shared'],
    holds: () => {
      const both = edge('network-tf1-both-releases', demoRefs.forward)
      const resolved = term('resolve-tf1-shared', 'SYN-TF1')
      return (
        edge('network-tf1', demoRefs.forward).effect === 'inhibiting' &&
        edge('network-tf1-v1', demoRefs.forward).effect === 'activating' &&
        both.effect === 'mixed' &&
        both.assertion_count === 2 &&
        both.evidence_count === 1 &&
        resolved.status === 'resolved' &&
        resolved.candidates[0].matches.map((match) => match.kind).join() === 'primary,label'
      )
    },
  },
  {
    row: 'syn-02',
    entries: ['network-tf1'],
    holds: () => {
      const reverse = edge('network-tf1', demoRefs.reverse)
      return reverse.source === demoRefs.g1 && reverse.target === demoRefs.tf1 && reverse.effect === 'inhibiting'
    },
  },
  {
    row: 'syn-03',
    entries: ['relationship-mixed', 'evidence-mixed'],
    holds: () => mixedEdgeHolds() && twoPublicationsHold() && recordKeys('evidence-mixed').includes('syn-03'),
  },
  {
    row: 'syn-04',
    entries: ['relationship-mixed', 'evidence-mixed'],
    holds: () => mixedEdgeHolds() && twoPublicationsHold() && recordKeys('evidence-mixed').includes('syn-04'),
  },
  {
    row: 'syn-05',
    entries: ['network-tf1'],
    holds: () => edge('network-tf1', demoRefs.unknownEffect).effect_counts.unknown === 1,
  },
  {
    row: 'syn-06',
    entries: ['network-all-edges'],
    holds: () => {
      const notStated = edge('network-all-edges', demoRefs.notStated)
      return notStated.effect === 'unknown' && notStated.effect_counts.not_stated === 1
    },
  },
  {
    row: 'syn-07',
    entries: ['network-tf1', 'relationship-tested-absent'],
    holds: () => {
      const network = recorded<GraphNetworkResponse>('network-tf1').body
      const tested = relationship('relationship-tested-absent').edge
      return (
        network.page.hidden.tested_absent === 1 &&
        !network.edges.some((candidate) => candidate.id === demoRefs.testedAbsent) &&
        tested.shown === false &&
        tested.effect === null
      )
    },
  },
  {
    row: 'syn-08',
    entries: ['network-all-edges'],
    holds: () => {
      const selfLoop = edge('network-all-edges', demoRefs.selfLoop)
      return selfLoop.source === selfLoop.target
    },
  },
  {
    row: 'syn-09',
    entries: ['evidence-scores'],
    holds: () => {
      const rows = evidence('evidence-scores')
      return (
        rows.length === 1 &&
        rows[0].evidence_basis === 'computational_prediction' &&
        rows[0].publications.length === 0 &&
        rows[0].scores.length === 2
      )
    },
  },
  {
    row: 'syn-10',
    entries: ['evidence-physical', 'network-all-edges'],
    holds: () => {
      const physical = edge('network-all-edges', demoRefs.symmetric)
      return (
        recordKeys('evidence-physical').includes('syn-10') &&
        physical.level === 'direct_interaction' &&
        physical.assertion_count === 2 &&
        physical.evidence_count === 1
      )
    },
  },
  {
    row: 'syn-11',
    entries: ['evidence-physical', 'network-all-edges'],
    holds: () => {
      const physical = edge('network-all-edges', demoRefs.symmetric)
      return (
        recordKeys('evidence-physical').includes('syn-11') &&
        physical.level === 'direct_interaction' &&
        physical.assertion_count === 2 &&
        physical.evidence_count === 1
      )
    },
  },
  {
    row: 'syn-12',
    entries: ['entity-tf1'],
    holds: () => entity('entity-tf1').reference_relationships[0].type === 'encodes',
  },
  {
    row: 'syn-13',
    entries: ['entity-p1'],
    holds: () => entity('entity-p1').reference_relationships.some((reference) => reference.type === 'isoform_of'),
  },
  {
    row: 'syn-14',
    entries: ['resolve-alpha-variant'],
    holds: () => {
      const first = recorded<GraphResolveResponse>('resolve-alpha-variant').body.results[0]
      return first.status === 'candidate' && candidates(first).includes(demoRefs.g3)
    },
  },
  {
    row: 'syn-15',
    entries: ['resolve-tf1-shared', 'network-all-edges'],
    holds: () =>
      candidates(term('resolve-tf1-shared', 'SYN-SHARED')).includes(demoRefs.g6) &&
      edge('network-all-edges', demoRefs.twoPapers).distinct.publications === 2,
  },
  {
    row: 'syn-16',
    entries: ['resolve-tf1-shared', 'evidence-tested-for'],
    holds: () =>
      candidates(term('resolve-tf1-shared', 'SYN-SHARED')).includes(demoRefs.p3) &&
      evidenceRow('evidence-tested-for', 'syn-16').interaction_level === 'not_stated',
  },
  {
    row: 'syn-17',
    entries: ['evidence-tested-for', 'network-all-edges'],
    holds: () => {
      const row = evidenceRow('evidence-tested-for', 'syn-17')
      return (
        row.outcome === 'tested_absent' &&
        row.interaction_level === 'direct_interaction' &&
        edge('network-all-edges', demoRefs.testedFor).tested_levels.tested_absent.includes('direct_interaction')
      )
    },
  },
]

describe('the README cases', () => {
  it.each(README_CASES)('$row is visible', ({ row, entries, holds }) => {
    expect(holds(), `${row} in ${entries.join(', ')}`).toBe(true)
  })
})

describe('network-all-edges', () => {
  const { edges, nodes } = recorded<GraphNetworkResponse>('network-all-edges').body

  it('holds the twelve non-reference relationships', () => {
    const expected = [
      demoRefs.symmetric,
      demoRefs.testedFor,
      demoRefs.reverse,
      demoRefs.forward,
      demoRefs.testedAbsent,
      demoRefs.unknownEffect,
      demoRefs.mixed,
      demoRefs.selfLoop,
      demoRefs.oneLabel,
      demoRefs.notStated,
      demoRefs.prediction,
      demoRefs.twoPapers,
    ]
    expect(edges.map((candidate) => candidate.id).sort(), 'network-all-edges').toEqual([...expected].sort())
  })

  it('holds twelve nodes', () => {
    expect(nodes, 'network-all-edges').toHaveLength(12)
  })

  it.each(['activating', 'inhibiting', 'mixed', 'unknown', 'not_applicable', null])('holds an effect of %s', (effect) => {
    expect(edges.map((candidate) => candidate.effect), 'network-all-edges').toContain(effect)
  })
})
