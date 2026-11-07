/**
 * Shared adaptive-quality state. Plain mutable object, read it inside useFrame / useMemo; never put it in React state.
 * Written only by `stepTier` (World.tsx drives it from drei's PerformanceMonitor) and `readRenderer` (Canvas onCreated).
 * `?tier=high|medium|low` pins a tier (no adaptation), handy for benchmarking.
 *
 * - `tier`:    "high" | "medium" | "low". Heavy modules can scale their own cost with it, e.g. Pedestrians/Traffic
 *              instance caps (high 100 % / medium 60 % / low 30 %), rain DROPS, building draw distance.
 *              Decisions made in useMemo should re-run on a tier change, so also read `perf.tier` inside useFrame
 *              and rebuild only when it actually changes.
 * - `dprCap`:  the device pixel ratio the Canvas currently renders at (high min(devicePixelRatio, 2), medium 1.5, low 1).
 * - `shadowSize`: sun shadow-map edge in texels (4096 / 2048 / 0 = shadows off); Lights.tsx applies it.
 * - `ao`:      screen-space AO quality, "high" | "medium" | "off" (always half-res: full-res costs ~5 ms more at 5 MP); Post.tsx applies it.
 * - `renderer`: UNMASKED_RENDERER_WEBGL, e.g. "ANGLE (Apple, ANGLE Metal Renderer: Apple M4 Pro, Unspecified Version)".
 * - `software`: true when the renderer string is SwiftShader / llvmpipe / a software rasteriser.
 */
export type PerfTier = "high" | "medium" | "low";

const TIERS: PerfTier[] = ["low", "medium", "high"];
const MAX_DPR = 2;
const OSCILLATION_MS = 45_000;

const TIER_FX: Record<PerfTier, { dpr: number; shadowSize: number; ao: "high" | "medium" | "off" }> = {
  high: { dpr: MAX_DPR, shadowSize: 4096, ao: "high" },
  medium: { dpr: 1.5, shadowSize: 2048, ao: "medium" },
  low: { dpr: 1, shadowSize: 0, ao: "off" },
};

function deviceCap(max: number) {
  return typeof window === "undefined" ? 1 : Math.min(window.devicePixelRatio || 1, max);
}

function pinnedTier(): PerfTier | null {
  if (typeof window === "undefined") return null;
  const q = new URLSearchParams(window.location.search).get("tier");
  return q === "high" || q === "medium" || q === "low" ? q : null;
}

const pinned = pinnedTier();

/** `?fx=noao,nosmaa,nobloom,noshadow` switches single effects off for A/B frame-time tests. */
export function fxOff(name: string) {
  return typeof window !== "undefined" && (new URLSearchParams(window.location.search).get("fx") ?? "").split(",").includes(name);
}

function apply(tier: PerfTier) {
  const fx = TIER_FX[tier];
  perf.tier = tier;
  perf.dprCap = deviceCap(fx.dpr);
  perf.shadowSize = fx.shadowSize;
  perf.ao = fx.ao;
}

export const perf = {
  tier: "high" as PerfTier,
  dprCap: 1,
  shadowSize: 0,
  ao: "off" as "high" | "medium" | "off",
  renderer: "",
  software: false,
};
apply(pinned ?? "high");

let ceiling = TIERS.length - 1;
let lastIncline = 0;

/**
 * Moves one tier up or down and returns the pixel ratio to render at. A decline that comes within 45 s of an
 * incline means the higher tier is not sustainable, so it becomes the ceiling and the quality settles instead of oscillating.
 */
export function stepTier(direction: 1 | -1, now = performance.now()) {
  if (pinned) return perf.dprCap;
  const index = TIERS.indexOf(perf.tier);
  if (direction < 0 && index > 0) {
    if (now - lastIncline < OSCILLATION_MS) ceiling = index - 1;
    apply(TIERS[index - 1]);
  } else if (direction > 0 && index < ceiling) {
    lastIncline = now;
    apply(TIERS[index + 1]);
  }
  return perf.dprCap;
}

const SOFTWARE = /swiftshader|llvmpipe|software|softpipe/i;

/** Reads the real GPU name once, stores it on `perf` and warns only when WebGL fell back to software rendering. */
export function readRenderer(gl: WebGLRenderingContext | WebGL2RenderingContext) {
  const info = gl.getExtension("WEBGL_debug_renderer_info");
  perf.renderer = String(gl.getParameter(info ? info.UNMASKED_RENDERER_WEBGL : gl.RENDERER));
  perf.software = SOFTWARE.test(perf.renderer);
  if (perf.software) {
    console.warn(
      `[twin] WebGL is running on a software renderer (${perf.renderer}). Enable hardware acceleration and check chrome://gpu.`,
    );
  }
  return perf.renderer;
}
