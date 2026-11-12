import { createElement } from 'react'
import type { ComponentType } from 'react'
import type { Core } from 'cytoscape'

export interface CytoscapeRunLog { instances: Core[]; runs: Map<Core, number> }

export function newRunLog(): CytoscapeRunLog {
  return { instances: [], runs: new Map() }
}

/**
 * For `vi.mock('react-cytoscapejs', ...)` in a test that renders a canvas: the real
 * component, so its diffing and patching run, made headless with styles on, since
 * jsdom has no canvas and the real renderer would make render() throw a blank error.
 * With a log, each new instance is recorded and given a `layoutstart` counter before
 * the caller's `cy` callback runs; the constructor's own layout has run by then, so
 * only the component's runs are counted.
 */
export async function headlessCytoscapeModule(importOriginal: () => Promise<unknown>, log?: CytoscapeRunLog): Promise<Record<string, unknown>> {
  const original = (await importOriginal()) as Record<string, unknown> & { default: ComponentType<Record<string, unknown>> }
  const Real = original.default
  function HeadlessCytoscape(props: Record<string, unknown>) {
    const given = props.cy as ((instance: Core) => void) | undefined
    const cy = (instance: Core) => {
      if (log && !log.runs.has(instance)) {
        log.instances.push(instance)
        log.runs.set(instance, 0)
        instance.on('layoutstart', () => {
          log.runs.set(instance, (log.runs.get(instance) ?? 0) + 1)
        })
      }
      given?.(instance)
    }
    return createElement(Real, { ...props, headless: true, styleEnabled: true, cy })
  }
  return { ...original, default: HeadlessCytoscape }
}
