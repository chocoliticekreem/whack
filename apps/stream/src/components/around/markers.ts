import type { AroundBusStop, AroundCam, AroundLiveCam, AroundStation, CamReading } from "@/platform/around/types";

const SVG_OPEN =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">';

export const VIDEO_ICON = `${SVG_OPEN}<path d="m16 13 5.223 3.482a.5.5 0 0 0 .777-.416V7.87a.5.5 0 0 0-.752-.432L16 10.5"/><rect x="2" y="6" width="14" height="12" rx="2"/></svg>`;
const USER_ICON = `${SVG_OPEN}<circle cx="12" cy="8" r="5"/><path d="M20 21a8 8 0 0 0-16 0"/></svg>`;
const CAR_ICON = `${SVG_OPEN}<path d="M19 17h2c.6 0 1-.4 1-1v-3c0-.9-.7-1.7-1.5-1.9C18.7 10.6 16 10 16 10s-1.3-1.4-2.2-2.3c-.5-.4-1.1-.7-1.8-.7H5c-.6 0-1.1.4-1.4.9l-1.4 2.9A3.7 3.7 0 0 0 2 12v4c0 .6.4 1 1 1h2"/><circle cx="7" cy="17" r="2"/><path d="M9 17h6"/><circle cx="17" cy="17" r="2"/></svg>`;

export type Crowd = "calm" | "busy" | "crowded";

export function crowdLevel(people: number): Crowd {
  if (people >= 20) return "crowded";
  if (people >= 8) return "busy";
  return "calm";
}

const HALO: Record<Crowd, { size: number; color: string }> = {
  calm: { size: 44, color: "rgba(61,220,151,0.28)" },
  busy: { size: 64, color: "rgba(255,176,32,0.3)" },
  crowded: { size: 88, color: "rgba(255,45,85,0.34)" },
};

export function camPinClass(active: boolean, selected: boolean) {
  const base =
    "relative grid size-8 cursor-pointer place-items-center rounded-full border-2 shadow-[0_6px_18px_rgba(0,0,0,0.6)] transition-transform hover:scale-110";
  const tone = active ? "border-ember bg-ember text-bg" : "border-line-strong bg-surface-2 text-text";
  return `${base} ${tone} ${selected ? "scale-125 ring-2 ring-text/70" : ""}`;
}

function escapeAttr(s: string) {
  return s.replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;");
}

export function camPinHtml(cam: AroundCam, active: boolean, reading: CamReading | undefined) {
  const parts: string[] = [];
  if (reading) {
    const { size, color } = HALO[crowdLevel(reading.people)];
    parts.push(
      `<span class="pointer-events-none absolute left-1/2 top-1/2 -z-10 -translate-x-1/2 -translate-y-1/2 rounded-full" style="width:${size}px;height:${size}px;background:radial-gradient(circle, ${color} 0%, transparent 72%)"></span>`,
    );
  }
  parts.push(`<span class="block size-4 [&>svg]:size-4">${VIDEO_ICON}</span>`);
  if (reading) {
    parts.push(
      `<span class="pointer-events-none absolute left-1/2 top-full mt-1 flex -translate-x-1/2 items-center gap-1 whitespace-nowrap rounded-md border border-line-strong bg-bg/90 px-1.5 py-0.5 font-mono text-[10px] font-semibold text-text [&_svg]:size-3 [&_svg]:text-muted">${USER_ICON}${reading.people}<span class="text-dim">·</span>${CAR_ICON}${reading.vehicles}</span>`,
    );
  } else if (active) {
    parts.push(
      '<span class="pointer-events-none absolute left-1/2 top-full mt-1 -translate-x-1/2 whitespace-nowrap rounded-md bg-ember px-1.5 py-0.5 text-[10px] font-semibold text-bg">Your feed</span>',
    );
  }
  return `<span class="relative grid place-items-center" aria-label="${escapeAttr(cam.name)}">${parts.join("")}</span>`;
}

export type StationCrowd = "quiet" | "normal" | "crowded";

/** TfL's figure is a share of a normal day's footfall for this time slot. */
export function stationCrowd(share: number | null): StationCrowd | null {
  if (share === null) return null;
  if (share >= 1.2) return "crowded";
  if (share < 0.6) return "quiet";
  return "normal";
}

const STATION_TONE: Record<StationCrowd, string> = {
  quiet: "border-ok/70 text-ok",
  normal: "border-line-strong text-text",
  crowded: "border-live text-live",
};

export function lineTrouble(station: AroundStation) {
  return station.lines.filter((l) => l.status !== "Good Service");
}

export function stationPinHtml(station: AroundStation, selected: boolean) {
  const crowd = stationCrowd(station.crowding);
  const tone = crowd ? STATION_TONE[crowd] : "border-line-strong text-muted";
  const trouble = lineTrouble(station).length > 0;
  const label =
    station.crowding !== null
      ? `${Math.round(station.crowding * 100)}%`
      : trouble
        ? "!"
        : "";
  return `<span class="relative block" aria-label="${escapeAttr(station.name)}"><span class="grid size-6 place-items-center rounded-full border-2 bg-bg ${tone} ${
    selected ? "ring-2 ring-text/70" : ""
  } shadow-[0_4px_12px_rgba(0,0,0,0.6)]"><span class="block h-[3px] w-4 rounded-sm bg-current"></span></span>${
    label
      ? `<span class="pointer-events-none absolute left-1/2 top-full mt-0.5 -translate-x-1/2 whitespace-nowrap rounded bg-bg/85 px-1 py-px font-mono text-[9px] font-semibold ${
          trouble && !crowd ? "text-warn" : "text-text/85"
        }">${label}</span>`
      : ""
  }</span>`;
}

export function liveCamPinHtml(cam: AroundLiveCam, selected: boolean) {
  return `<span class="relative grid place-items-center" aria-label="${escapeAttr(cam.name)}"><span class="grid size-7 place-items-center rounded-full border-2 border-[#b388ff] bg-[#2a1f45] text-[#d9c8ff] shadow-[0_6px_18px_rgba(0,0,0,0.6)] ${
    selected ? "scale-125 ring-2 ring-text/70" : ""
  }"><span class="block size-3.5 [&>svg]:size-3.5">${VIDEO_ICON}</span></span><span class="pointer-events-none absolute left-1/2 top-full mt-1 -translate-x-1/2 whitespace-nowrap rounded bg-bg/85 px-1 py-px font-mono text-[9px] font-semibold text-[#d9c8ff]">HD live</span></span>`;
}

export function formatCountdown(seconds: number) {
  if (seconds <= 30) return "due";
  return `${Math.max(1, Math.round(seconds / 60))} min`;
}

export function busStopHtml(stop: AroundBusStop, nextSeconds: number | null, selected: boolean) {
  const letter = stop.indicator.replace(/[^A-Za-z0-9]/g, "").slice(0, 2) || "•";
  const countdown =
    nextSeconds === null
      ? ""
      : `<span class="pointer-events-none absolute left-1/2 top-full mt-0.5 -translate-x-1/2 whitespace-nowrap rounded bg-bg/85 px-1 py-px font-mono text-[9px] font-semibold ${
          nextSeconds <= 30 ? "text-ok" : "text-text/85"
        }">${formatCountdown(nextSeconds)}</span>`;
  return `<span class="relative block" aria-label="${escapeAttr(stop.name)}"><span class="grid size-5 place-items-center rounded-[5px] border ${
    selected ? "border-text bg-text text-bg" : "border-[#c8342b] bg-[#e0352b] text-white"
  } text-[9px] font-bold leading-none shadow-[0_4px_12px_rgba(0,0,0,0.6)]">${letter}</span>${countdown}</span>`;
}
