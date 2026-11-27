import ts from 'typescript'
import { describe, expect, it } from 'vitest'

const ROOT = ts.sys.getCurrentDirectory() // frontend/, where vitest runs
const SINGLE_FILES = ['src/features/graph/types.ts', 'src/mocks/handlers/typedGraph.ts', 'src/features/graph/viewState.ts']
const DIRECTORIES = ['src/api/graph', 'src/mocks/fixtures/typedGraph', 'src/features/graph/network', 'src/features/graph/legend']
const TSX_DIRECTORIES = ['src/features/graph/panels', 'src/features/graph/tables', 'src/features/browsers', 'src/features/graph/pages', 'src/features/graph/badge', 'src/features/graph/words', 'src/features/graph/workspace', 'src/features/graph/filters', 'src/features/graph/search', 'src/features/explore', 'src/features/entities']
const PROBE = `${ROOT}/src/features/graph/__strict_probe__.ts`
const PROBE_TEXT = `
import type { GraphEvidenceRow, GraphNode } from './types'
export const node: GraphNode = {
  id: 'entity:1', type: 'gene', label: null, taxon: null, status: null, primary_identifier: null,
}
export function key(row: GraphEvidenceRow): string {
  return row.record.key
}
`

function typedGraphFiles(): string[] {
  const inDirectories = [
    ...DIRECTORIES.flatMap((directory) => ts.sys.readDirectory(`${ROOT}/${directory}`, ['.ts'])),
    ...TSX_DIRECTORIES.flatMap((directory) => ts.sys.readDirectory(`${ROOT}/${directory}`, ['.ts', '.tsx'])),
  ]
  return [...SINGLE_FILES.map((file) => `${ROOT}/${file}`), ...inDirectories.filter((file) => !/\.test\.tsx?$/.test(file))]
}

function strictProgram(rootNames: string[]): ts.Program {
  const { config } = ts.readConfigFile(`${ROOT}/tsconfig.json`, ts.sys.readFile)
  const options: ts.CompilerOptions = {
    ...ts.parseJsonConfigFileContent(config, ts.sys, ROOT).options,
    strict: true,
    noEmit: true,
  }
  const host = ts.createCompilerHost(options)
  const getSourceFile = host.getSourceFile.bind(host)
  const fileExists = host.fileExists.bind(host)
  host.getSourceFile = (name, languageVersion, ...rest) =>
    name === PROBE ? ts.createSourceFile(name, PROBE_TEXT, languageVersion) : getSourceFile(name, languageVersion, ...rest)
  host.fileExists = (name) => name === PROBE || fileExists(name)
  return ts.createProgram({ rootNames, options, host })
}

function problems(program: ts.Program, name: string): string[] {
  const source = program.getSourceFile(name)
  if (!source) return [`${name} is not in the program`]
  return [...program.getSyntacticDiagnostics(source), ...program.getSemanticDiagnostics(source)].map(
    (diagnostic) => `${name}: TS${diagnostic.code} ${ts.flattenDiagnosticMessageText(diagnostic.messageText, '\n')}`,
  )
}

describe("the typed graph's files under strict", () => {
  it('compile, and strict is really on', () => {
    expect(ts.sys.fileExists(`${ROOT}/tsconfig.json`)).toBe(true)
    const files = typedGraphFiles()
    expect(files.length).toBeGreaterThan(20)
    expect([...DIRECTORIES, ...TSX_DIRECTORIES].filter((directory) => !files.some((file) => file.startsWith(`${ROOT}/${directory}/`)))).toEqual([])
    const program = strictProgram([...files, PROBE])
    expect(files.flatMap((file) => problems(program, file))).toEqual([])
    const probe = problems(program, PROBE)
    expect(probe).toHaveLength(2)
    expect(probe.join('\n')).toMatch(/null/)
  }, 120_000)
})
