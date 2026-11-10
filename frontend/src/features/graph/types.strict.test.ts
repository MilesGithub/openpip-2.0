import ts from 'typescript'
import { describe, expect, it } from 'vitest'

const ROOT = ts.sys.getCurrentDirectory() // frontend/, where vitest runs
const SINGLE_FILES = ['src/features/graph/types.ts', 'src/mocks/handlers/typedGraph.ts']
const DIRECTORIES = ['src/api/graph', 'src/mocks/fixtures/typedGraph']
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

function f1Files(): string[] {
  const inDirectories = DIRECTORIES.flatMap((directory) => ts.sys.readDirectory(`${ROOT}/${directory}`, ['.ts']))
  return [...SINGLE_FILES.map((file) => `${ROOT}/${file}`), ...inDirectories.filter((file) => !file.endsWith('.test.ts'))]
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

describe("F1's files under strict", () => {
  it('compile, and strict is really on', () => {
    expect(ts.sys.fileExists(`${ROOT}/tsconfig.json`)).toBe(true)
    const files = f1Files()
    expect(files.length).toBeGreaterThan(20)
    const program = strictProgram([...files, PROBE])
    expect(files.flatMap((file) => problems(program, file))).toEqual([])
    const probe = problems(program, PROBE)
    expect(probe).toHaveLength(2)
    expect(probe.join('\n')).toMatch(/null/)
  }, 120_000)
})
