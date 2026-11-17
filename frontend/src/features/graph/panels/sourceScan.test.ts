import ts from 'typescript'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

beforeEach(() => {
  vi.spyOn(console, 'warn')
  vi.spyOn(console, 'error')
})

afterEach(() => {
  // Nothing renders here, so there is nothing to unmount before the calls are read.
  const calls = [...vi.mocked(console.warn).mock.calls, ...vi.mocked(console.error).mock.calls]
  vi.restoreAllMocks()
  expect(calls.map((args) => args.map(String).join(' ').slice(0, 300))).toEqual([])
})

const ROOT = ts.sys.getCurrentDirectory() // frontend/, where vitest runs, as the strict test finds it
const FOLDERS = ['src/features/graph/panels', 'src/features/graph/tables']
// The files that render source text; the scan asserts no file count, so merging two modules fails nothing.
const RENDERING = [
  'src/features/graph/panels/RelationshipPanel.tsx',
  'src/features/graph/panels/EvidenceList.tsx',
  'src/features/graph/panels/EntityPanel.tsx',
  'src/features/graph/panels/ReleaseList.tsx',
  'src/features/graph/tables/TypedEdgesTable.tsx',
]
// Names through which a string reaches an HTML sink, or an element's props reach it unnamed.
const SINK_NAMES = new Set([
  'dangerouslySetInnerHTML', 'innerHTML', 'outerHTML', 'insertAdjacentHTML', 'createContextualFragment', 'srcdoc', 'srcDoc',
  'writeln', 'parseFromString', 'createElement', 'cloneElement', 'setAttribute', 'setAttributeNS', 'Reflect', 'execCommand',
])
const SINK_ELEMENTS = new Set(['script', 'style', 'iframe', 'frame', 'object', 'embed', 'base', 'link', 'meta'])

/** The syntax, not the text: a regex would also hit a comment that names a sink. */
function scan(name: string, text: string): string[] {
  const kind = name.endsWith('.tsx') ? ts.ScriptKind.TSX : ts.ScriptKind.TS
  const source = ts.createSourceFile(name, text, ts.ScriptTarget.ES2020, true, kind)
  const hits: string[] = []
  const at = (node: ts.Node) => source.getLineAndCharacterOfPosition(node.getStart(source)).line + 1
  const visit = (node: ts.Node): void => {
    if ((ts.isIdentifier(node) || ts.isPrivateIdentifier(node) || ts.isStringLiteralLike(node)) && SINK_NAMES.has(node.text)) {
      hits.push(`${node.text} at line ${at(node)}`)
    }
    if (ts.isPropertyAccessExpression(node) && node.name.text === 'write') hits.push(`.write at line ${at(node)}`)
    if (ts.isJsxSpreadAttribute(node)) hits.push(`a JSX spread at line ${at(node)}`)
    if ((ts.isJsxOpeningElement(node) || ts.isJsxSelfClosingElement(node)) && ts.isIdentifier(node.tagName)
      && SINK_ELEMENTS.has(node.tagName.text)) {
      hits.push(`<${node.tagName.text}> at line ${at(node)}`)
    }
    ts.forEachChild(node, visit)
  }
  visit(source)
  return hits
}

// S-1 `scan`'s sixteen samples, copied in since the critique's probes are not in the repository: each must hit, so the scan can fail.
const SAMPLES: Record<string, string> = {
  'control.tsx': 'export const A = ({ t }: { t: string }) => <p dangerouslySetInnerHTML={{ __html: t }} />',
  'controlInner.ts': 'export function B(el: HTMLElement, t: string) { el.innerHTML = t }',
  'iframeSrcDoc.tsx': 'export const C = ({ t }: { t: string }) => <iframe title="x" srcDoc={t} />',
  'createElementProps.tsx': "import { createElement } from 'react'\nexport const D = (q: Record<string, unknown>) => createElement('span', q)",
  'cloneElementProps.tsx': "import { cloneElement } from 'react'\nimport type { ReactElement } from 'react'\nexport const E = (el: ReactElement, q: Record<string, unknown>) => cloneElement(el, q)",
  'computedKey.tsx': "import { createElement } from 'react'\nexport const F = (t: string) => createElement('p', { ['dangerously' + 'SetInnerHTML']: { __html: t } })",
  'computedLiteralKey.tsx': "import { createElement } from 'react'\nexport const G = (t: string) => createElement('p', { ['dangerouslySetInnerHTML']: { __html: t } })",
  'objectAssign.ts': 'export function H(el: HTMLElement, t: string) { Object.assign(el, { innerHTML: t }) }',
  'reflectSet.ts': "export function I(el: HTMLElement, t: string) { Reflect.set(el, 'innerHTML', t) }",
  'setAttributeSrcdoc.ts': "export function J(el: HTMLIFrameElement, t: string) { el.setAttribute('srcdoc', t) }",
  'srcdocProperty.ts': 'export function K(el: HTMLIFrameElement, t: string) { el.srcdoc = t }',
  'writeln.ts': 'export function L(t: string) { document.writeln(t) }',
  'windowDocumentWrite.ts': 'export function M(t: string) { window.document.write(t) }',
  'aliasWrite.ts': 'export function N(t: string) { const d = document; d.write(t) }',
  'domParser.ts': "export function O(el: HTMLElement, t: string) { el.append(...Array.from(new DOMParser().parseFromString(t, 'text/html').body.childNodes)) }",
  'spec124.tsx': 'function RelationshipContent(props: { id: string }) { return <p>{props.id}</p> }\nexport function P(props: { id: string }) { return <RelationshipContent key={props.id} {...props} /> }',
}

describe('the panels and the table render source text as text', () => {
  it('SCAN-1: no source names an HTML sink, spreads JSX props or renders a dangerous element; every sample hits', () => {
    const files = FOLDERS
      .flatMap((folder) => ts.sys.readDirectory(`${ROOT}/${folder}`, ['.ts', '.tsx']))
      .filter((file) => !/\.test\.tsx?$/.test(file))
    for (const file of RENDERING) expect(files, file).toContain(`${ROOT}/${file}`)
    for (const folder of FOLDERS) expect(files.some((file) => file.startsWith(`${ROOT}/${folder}/`)), folder).toBe(true)
    const found = files.map((file) => {
      const text = ts.sys.readFile(file) ?? ''
      expect(text.length, file).toBeGreaterThan(0)
      return { file: file.slice(ROOT.length + 1), hits: scan(file, text) }
    })
    expect(found.filter((entry) => entry.hits.length > 0)).toEqual([])

    expect(Object.keys(SAMPLES)).toHaveLength(16)
    const missed = Object.entries(SAMPLES).filter(([name, text]) => scan(name, text).length === 0).map(([name]) => name)
    expect(missed).toEqual([])
  })
})
