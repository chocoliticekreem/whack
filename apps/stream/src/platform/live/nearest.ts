import { LOCATIONS } from "@/lib/locations";
import type { StreamLocation } from "@/lib/types";
import { distanceM } from "@/platform/around/geo";

interface Place {
  name: string;
  lat: number;
  lon: number;
}

/** Named places around Tower Hill the phone is labelled against (OSM, 2026-10-17). */
const PLACES: Place[] = [
  { name: "Tower Hill station", lat: 51.5098, lon: -0.0766 },
  { name: "Tower of London", lat: 51.5081, lon: -0.0759 },
  { name: "Tower Bridge", lat: 51.5055, lon: -0.0754 },
  { name: "Tower Bridge north approach", lat: 51.5073, lon: -0.0745 },
  { name: "St Katharine Docks", lat: 51.5066, lon: -0.0706 },
  { name: "Tower Gateway DLR", lat: 51.5106, lon: -0.0743 },
  { name: "Trinity Square Gardens", lat: 51.5097, lon: -0.0779 },
  { name: "All Hallows by the Tower", lat: 51.5094, lon: -0.0794 },
  { name: "Tower Pier", lat: 51.5078, lon: -0.0783 },
  { name: "Fenchurch Street station", lat: 51.5117, lon: -0.0788 },
  { name: "Minories", lat: 51.5115, lon: -0.0748 },
  { name: "Royal Mint Court", lat: 51.5094, lon: -0.0727 },
  { name: "20 Fenchurch Street", lat: 51.5112, lon: -0.0836 },
  { name: "Potters Fields Park", lat: 51.5043, lon: -0.0772 },
  { name: "Tooley Street", lat: 51.5049, lon: -0.0806 },
  { name: "Shad Thames", lat: 51.5033, lon: -0.0727 },
  ...LOCATIONS.map((l) => ({ name: l.name, lat: l.lat, lon: l.lon })),
];

function nearest<T extends Place>(list: T[], lat: number, lon: number) {
  let best = list[0];
  let bestD = Infinity;
  for (const p of list) {
    const d = distanceM(lat, lon, p.lat, p.lon);
    if (d < bestD) {
      bestD = d;
      best = p;
    }
  }
  return { place: best, distanceM: bestD };
}

export function labelFor(lat: number, lon: number) {
  const { place, distanceM: d } = nearest(PLACES, lat, lon);
  return d < 40 ? place.name : `${Math.round(d / 10) * 10} m from ${place.name}`;
}

/** A StreamLocation built from a real or relayed position: nearest named place, nearest camera. */
export function gpsLocation(lat: number, lon: number, label: string, area: string): StreamLocation {
  const near = nearest(LOCATIONS, lat, lon).place;
  return { id: "gps", name: label, area, lat, lon, camera: near.camera };
}
