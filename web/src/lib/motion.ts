/**
 * Motion helpers, on top of GSAP core.
 *
 * Three rules hold everywhere in here, because animation on a dashboard is only
 * worth having if it stays cheap:
 *
 * 1. **Transform and opacity only.** Both are composited on the GPU. Animating
 *    `width` or `height` would put layout back in the frame budget — the split
 *    bar grows with `scaleX`, not `width`, for exactly this reason.
 * 2. **Once per value, not once per render.** Every hook keys off the data it
 *    is showing, so React re-rendering for an unrelated reason does not replay
 *    the animation.
 * 3. **`prefers-reduced-motion` short-circuits to the final state.** Not a
 *    faster animation — no animation, with the finished value painted directly.
 *
 * Only `gsap` core is imported. Plugins (ScrollTrigger and friends) would more
 * than double what ships for effects this page does not need.
 */
import { useEffect, useLayoutEffect, useRef } from 'react'
import type { RefObject } from 'react'
import { gsap } from 'gsap'

/**
 * Runs before the browser paints, so an element never shows its final state for
 * one frame and then snaps back to the start of its animation. On the server
 * there is no paint and `useLayoutEffect` warns, so it falls back there.
 */
const useIsomorphicLayoutEffect =
  typeof window !== 'undefined' ? useLayoutEffect : useEffect

/**
 * Remove the inline styles a tween leaves behind.
 *
 * `clearProps` is not enough on its own. GSAP's CSSPlugin writes `translate`,
 * `rotate` and `scale` as their own CSS properties to stop them overriding the
 * `transform` matrix it controls, and it does not take them off again — naming
 * them in `clearProps` does not work either, because they are not properties it
 * maps. Left alone they are harmless (`none` is the default) but they sit on
 * every element the page ever animated.
 *
 * `clearProps: 'all'` would take them, but these rows carry an inline `cursor`
 * that React owns and GSAP has no business removing, so the properties are
 * named here instead and the attribute only goes if nothing else is left.
 */
const TWEENED = ['transform', 'opacity', 'translate', 'rotate', 'scale', 'will-change']

function stripInline(els: HTMLElement[]) {
  for (const el of els) {
    for (const prop of TWEENED) el.style.removeProperty(prop)
    if (!el.getAttribute('style')) el.removeAttribute('style')
  }
}

/**
 * Whether to skip animating and paint the finished state instead.
 *
 * Two reasons, and the second is not a preference but a correctness issue:
 *
 * - `prefers-reduced-motion` is set.
 * - The tab is hidden. A background tab throttles `requestAnimationFrame`, and
 *   GSAP is driven by it — so a tween started there applies its `from` state
 *   (`opacity: 0`) and then may never advance. Content that is only visible
 *   once an animation finishes is content that can stay invisible. Nobody is
 *   watching a hidden tab anyway, so there is nothing to lose by skipping it.
 *
 * SSR-safe: the server has no `matchMedia` and nothing to animate.
 */
export function skipMotion(): boolean {
  if (typeof window === 'undefined' || !window.matchMedia) return true
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return true
  if (typeof document !== 'undefined' && document.hidden) return true
  return false
}

/**
 * Count a number up to its real value.
 *
 * Writes `textContent` rather than driving React state — re-rendering a tree 60
 * times a second to animate one integer is the expensive way to do this.
 */
export function useCountUp(
  ref: RefObject<HTMLElement | null>,
  value: number,
  { duration = 0.9, delay = 0 }: { duration?: number; delay?: number } = {},
) {
  useIsomorphicLayoutEffect(() => {
    const el = ref.current
    if (!el) return

    const render = (n: number) => {
      el.textContent = Math.round(n).toLocaleString()
    }

    if (skipMotion() || value === 0) {
      render(value)
      return
    }

    // Start from zero rather than the previous value: these are "what arrived
    // this week" figures, and counting up from nothing reads as accumulation.
    const state = { n: 0 }
    const tween = gsap.to(state, {
      n: value,
      duration,
      delay,
      ease: 'power2.out',
      onUpdate: () => render(state.n),
      // Never leave a rounding artefact as the final rendered value.
      onComplete: () => render(value),
    })
    return () => {
      tween.kill()
      render(value)
    }
  }, [ref, value, duration, delay])
}

/**
 * Grow a bar segment from its leading edge.
 *
 * `scaleX` on a pre-sized element, so the browser composites rather than
 * reflowing. The element must carry `transform-origin: left`.
 */
export function useGrowBar(
  ref: RefObject<HTMLElement | null>,
  key: string | number,
  { duration = 0.85, delay = 0.1 }: { duration?: number; delay?: number } = {},
) {
  useIsomorphicLayoutEffect(() => {
    const el = ref.current
    if (!el) return

    if (skipMotion()) {
      stripInline([el])
      return
    }

    const tween = gsap.fromTo(
      el,
      { scaleX: 0 },
      { scaleX: 1, duration, delay, ease: 'power3.out' },
    )
    return () => {
      tween.kill()
      stripInline([el])
    }
  }, [ref, key, duration, delay])
}

/**
 * Fade a group of elements up as their data lands.
 *
 * `max` caps how many actually animate. A digest can render forty signal rows,
 * and putting forty elements on the compositor at once to stagger them costs
 * more than the effect is worth — everything past the cap is simply shown.
 */
export function useReveal(
  scope: RefObject<HTMLElement | null>,
  selector: string,
  {
    key,
    stagger = 0.045,
    duration = 0.5,
    delay = 0,
    y = 10,
    max = 14,
  }: {
    key?: string | number
    stagger?: number
    duration?: number
    delay?: number
    y?: number
    max?: number
  } = {},
) {
  useIsomorphicLayoutEffect(() => {
    const root = scope.current
    if (!root) return

    const all = Array.from(root.querySelectorAll<HTMLElement>(selector))
    if (all.length === 0) return

    if (skipMotion()) {
      stripInline(all)
      return
    }

    const animated = all.slice(0, max)
    const ctx = gsap.context(() => {
      gsap.fromTo(
        animated,
        { opacity: 0, y },
        {
          opacity: 1,
          y: 0,
          duration,
          delay,
          stagger,
          ease: 'power2.out',
          onComplete: () => stripInline(animated),
        },
      )
    }, root)

    return () => ctx.revert()
  }, [scope, selector, key, stagger, duration, delay, y, max])
}

/**
 * Grow every bar matched by `selector` inside `scope`.
 *
 * The same `scaleX` rule as `useGrowBar`, for lists of rails — the velocity
 * chart is a dozen of them. Matched elements need `transform-origin: left`.
 */
export function useGrowBars(
  scope: RefObject<HTMLElement | null>,
  selector: string,
  key: string | number,
  { duration = 0.7, delay = 0.1, stagger = 0.035, max = 14 }: {
    duration?: number; delay?: number; stagger?: number; max?: number
  } = {},
) {
  useIsomorphicLayoutEffect(() => {
    const root = scope.current
    if (!root) return

    const bars = Array.from(root.querySelectorAll<HTMLElement>(selector))
    if (bars.length === 0) return

    if (skipMotion()) {
      stripInline(bars)
      return
    }

    const growing = bars.slice(0, max)
    const ctx = gsap.context(() => {
      gsap.fromTo(
        growing,
        { scaleX: 0 },
        {
          scaleX: 1,
          duration,
          delay,
          stagger,
          ease: 'power3.out',
          onComplete: () => stripInline(growing),
        },
      )
    }, root)
    return () => ctx.revert()
  }, [scope, selector, key, duration, delay, stagger, max])
}

/**
 * Count up every figure matched by `selector` inside `scope`.
 *
 * Takes each element's own rendered text as the target, so rows in a list do
 * not each need a ref threaded down to them. The original string is put back on
 * completion, which keeps whatever formatting the component chose — a thousands
 * separator, a percent sign, a leading zero — rather than this deciding for it.
 */
export function useCountUpAll(
  scope: RefObject<HTMLElement | null>,
  selector: string,
  key: string | number,
  { duration = 0.7, delay = 0.1, stagger = 0.05, max = 12 }: {
    duration?: number; delay?: number; stagger?: number; max?: number
  } = {},
) {
  useIsomorphicLayoutEffect(() => {
    const root = scope.current
    if (!root) return

    const els = Array.from(root.querySelectorAll<HTMLElement>(selector)).slice(0, max)
    if (els.length === 0 || skipMotion()) return

    const targets = els.map((el) => {
      const text = el.textContent ?? ''
      return { el, text, value: Number(text.replace(/[^0-9.-]/g, '')) }
    })
    // A column that is not actually numeric must be left exactly as it is.
    const numeric = targets.filter((t) => Number.isFinite(t.value) && t.value !== 0)
    if (numeric.length === 0) return

    const tweens = numeric.map(({ el, text, value }, i) =>
      gsap.to({ n: 0 }, {
        n: value,
        duration,
        delay: delay + i * stagger,
        ease: 'power2.out',
        onUpdate() {
          el.textContent = String(Math.round((this.targets()[0] as { n: number }).n))
        },
        onComplete() {
          el.textContent = text
        },
      }),
    )

    return () => {
      for (const tw of tweens) tw.kill()
      // Whatever the interruption, the true value is what stays on screen.
      for (const { el, text } of numeric) el.textContent = text
    }
  }, [scope, selector, key, duration, delay, stagger, max])
}

/** A ref plus the count-up wired together, for the common single-figure case. */
export function useFigure(value: number, opts?: { duration?: number; delay?: number }) {
  const ref = useRef<HTMLSpanElement>(null)
  useCountUp(ref, value, opts)
  return ref
}
