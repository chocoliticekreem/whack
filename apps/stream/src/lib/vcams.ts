import type { AroundCam } from "@/platform/around/types";
import VCAMS from "./vcams.json";

/**
 * Virtual live cams: 15 min of sharp fixed street footage per camera, played as if it were live CCTV and
 * tracked ahead of time by YOLO26 (`services/vision/vcams.py`, served by services/yolo like a JamCam).
 * They sit at the real spot each video was filmed, so the twin can mirror them.
 */
interface VirtualCam {
  id: string;
  file: string;
  name: string;
  lat: number;
  lon: number;
  view: string;
  heading: number;
}

export const isVirtualCam = (id: string) => id.startsWith("VCAM_");

export function virtualCams(): Omit<AroundCam, "distanceM">[] {
  return (VCAMS as VirtualCam[]).map((c) => ({
    id: c.id,
    name: c.name,
    lat: c.lat,
    lon: c.lon,
    videoUrl: `/footage/cams/${c.file}.mp4`,
    imageUrl: `/footage/cams/${c.file}.jpg`,
    view: c.view,
    heading: c.heading,
  }));
}

/** Where a looping virtual cam is "now", so every viewer sees the same live moment. */
export const liveOffsetS = (duration: number) => (duration > 0 ? (Date.now() / 1000) % duration : 0);
