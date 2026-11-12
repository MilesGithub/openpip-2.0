import type { Collection, Core, Layouts, LayoutOptions } from 'cytoscape'
import type { TypedLayoutName } from './presentation'

const FRAME = { fit: true, padding: 30 } as const

export function prefersReducedMotion(): boolean {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return false
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches === true
}

/**
 * cose stays frame-sliced in both modes: run synchronously, it blocked the main thread for a
 * median 3.0 s at the default size and 11.0 s at the caps in the capped test container, while
 * the page handles input between frames. Under reduced motion an infinite animationThreshold
 * keeps every node still until the run ends. cola without animation runs synchronously until it
 * converges, which maxSimulationTime cannot stop (cytoscape-cola's src/cola.js), so under
 * reduced motion it runs cose's options instead. No layout is seeded (Y38).
 */
export function layoutOptions(name: TypedLayoutName, options: { reducedMotion: boolean }): LayoutOptions {
  if (name === 'cose' || (name === 'cola' && options.reducedMotion)) {
    const cose = { name: 'cose', animate: true, randomize: false, nodeDimensionsIncludeLabels: true, ...FRAME } as const
    return options.reducedMotion ? { ...cose, animationThreshold: Number.POSITIVE_INFINITY } : { ...cose }
  }
  if (name === 'cola') {
    // cytoscape's typings know none of cola's options.
    return {
      name: 'cola', animate: true, randomize: false, maxSimulationTime: 2000, nodeSpacing: 20, edgeLength: 90, ...FRAME,
    } as LayoutOptions
  }
  return { name, animate: !options.reducedMotion, ...FRAME }
}

/**
 * Ends a run so that nothing it does afterwards writes to the core. A stopped frame-sliced cose
 * run still runs one more frame, whose done() reads options.eles and options.fit again
 * (cytoscape's src/extensions/layout/cose.mjs). After an unmount react-cytoscapejs has already
 * destroyed the core, and that frame's writes would throw from endBatch; on a live core they
 * would overwrite the next run's positions. cola captures its nodes in run(), so for cola this
 * only ends its fitting.
 */
export function stopQuietly(cy: Core, run: Layouts): void {
  const pending = (run as unknown as { options?: { eles?: Collection; fit?: boolean } }).options
  if (pending) {
    pending.eles = cy.collection()
    pending.fit = false
  }
  run.stop()
}
