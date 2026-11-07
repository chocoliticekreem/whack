import type { StreamLocation } from "./types";

const cam = (id: string, label: string) => ({
  label: `${label} (TfL JamCam)`,
  url: `https://s3-eu-west-1.amazonaws.com/jamcams.tfl.gov.uk/${id}.mp4`,
});

// Each camera is the nearest available TfL JamCam to the location (TfL /Place/Type/JamCam, 2026-10-17).
export const LOCATIONS: StreamLocation[] = [
  { id: "tower-hill", name: "Tower Hill", area: "EC3N", lat: 51.5098, lon: -0.0766, camera: cam("00001.03500", "Tower Bridge App / East Smithfield") },
  { id: "tower-bridge", name: "Tower Bridge", area: "SE1", lat: 51.5055, lon: -0.0754, camera: cam("00001.03506", "Tooley St / Boss St") },
  { id: "st-katharine", name: "St Katharine Docks", area: "E1W", lat: 51.5066, lon: -0.0706, camera: cam("00001.02351", "The Highway / Vaughan Way") },
  { id: "leicester-sq", name: "Leicester Square", area: "WC2H", lat: 51.5103, lon: -0.1301, camera: cam("00001.07550", "Charing Cross Rd / Cranbourne St") },
  { id: "piccadilly", name: "Piccadilly Circus", area: "W1J", lat: 51.5101, lon: -0.1344, camera: cam("00001.07450", "Piccadilly Circus") },
  { id: "oxford-circus", name: "Oxford Circus", area: "W1B", lat: 51.5152, lon: -0.1418, camera: cam("00001.07451", "Regent St / Conduit St") },
  { id: "trafalgar-sq", name: "Trafalgar Square", area: "WC2N", lat: 51.508, lon: -0.1281, camera: cam("00001.06502", "Trafalgar Square") },
  { id: "camden", name: "Camden Market", area: "NW1", lat: 51.5413, lon: -0.1462, camera: cam("00001.09551", "Camden High St / Kentish Town Rd") },
  { id: "shoreditch", name: "Shoreditch High St", area: "E1", lat: 51.5233, lon: -0.0753, camera: cam("00001.01400", "Shoreditch High St / Commercial St") },
];
