import { ref, watch, type Ref } from "vue";

/**
 * Animate a numeric ref from its current displayed value to a new target
 * over `durationMs`, using ease-out cubic. Pure requestAnimationFrame —
 * no library. The returned ref is updated each frame and can be rendered
 * directly with `{{ count }}`.
 */
export function useCountUp(target: Ref<number>, durationMs = 1200): Ref<number> {
  const current = ref(0);
  let raf = 0;

  function animate(from: number, to: number) {
    // SSR has no requestAnimationFrame — skip animation entirely and just
    // set the displayed value. Client-side rehydration will pick up here
    // and animate on subsequent target changes.
    if (typeof window === "undefined" || typeof requestAnimationFrame === "undefined") {
      current.value = to;
      return;
    }
    cancelAnimationFrame(raf);
    const startTs = performance.now();
    const step = (now: number) => {
      const t = Math.min(1, (now - startTs) / durationMs);
      const eased = 1 - Math.pow(1 - t, 3);
      current.value = Math.round(from + (to - from) * eased);
      if (t < 1) raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
  }

  watch(
    target,
    (to) => animate(current.value, to ?? 0),
    { immediate: true },
  );
  return current;
}
