/** Contract for the "Around me" view. Chris's data layer can serve the same shapes. */

export interface AroundCam {
  id: string;
  name: string;
  lat: number;
  lon: number;
  videoUrl: string;
  imageUrl: string;
  /** TfL's free-text view description, e.g. "WEST-twds Blackfriars". */
  view: string;
  /** Compass bearing the camera faces, when the view text states it. */
  heading: number | null;
  distanceM: number;
}

export interface AroundDisruption {
  id: string;
  street: string;
  severity: string;
  category: string;
  closure: string;
  comments: string;
  /** [lon, lat] pairs, ready for GeoJSON. */
  line: [number, number][];
  distanceM: number;
}

/** YOLO detections for one camera clip (services/yolo). Peak counts across sampled frames. */
export interface CamReading {
  camId: string;
  people: number;
  vehicles: number;
  at: number;
  /** An annotated frame is available at /api/around/frame?id=camId. */
  frame: boolean;
}

export interface BusArrival {
  line: string;
  destination: string;
  /** Seconds until arrival, as of the stop's `at`. */
  seconds: number;
}

export interface AroundBusStop {
  id: string;
  name: string;
  /** Stop letter painted on the flag, e.g. "TH". */
  indicator: string;
  lat: number;
  lon: number;
  distanceM: number;
  arrivals: BusArrival[];
  at: number;
}


export type TrackedClass = "person" | "car" | "bus" | "truck" | "motorcycle" | "bicycle";

export interface TrackedObject {
  id: number;
  kind: "person" | "vehicle";
  /** YOLO class, majority vote over the clip. Absent from older YOLO servers. */
  cls?: TrackedClass;
  /** Median colour of the object in the real frame, "#rrggbb" (torso for people, body panel for vehicles). */
  color?: string;
  /** Median [width, height] of the box as frame fractions, from frames where it isn't cut by the edge. */
  box?: [number, number];
  /** [seconds into clip, u, v]: bottom-centre of the box, normalised to the frame. */
  pts: [number, number, number][];
}

/** One clip's YOLO + ByteTrack tracks (services/yolo /tracks). */
export interface CamClipTracks {
  camId: string;
  at: number;
  duration: number;
  tracks: TrackedObject[];
}

export interface StationLine {
  id: string;
  name: string;
  /** TfL status, e.g. "Good Service", "Minor Delays", "Part Closure". */
  status: string;
}

/** A tube/DLR/rail station with TfL's live crowding (share of a normal day's footfall) and line status. */
export interface AroundStation {
  id: string;
  name: string;
  lat: number;
  lon: number;
  distanceM: number;
  modes: string[];
  lines: StationLine[];
  /** 1.0 = a normal day's footfall for this time; null when TfL has no live data for the station. */
  crowding: number | null;
  crowdingAt: number | null;
}

export interface CrimeStreet {
  name: string;
  lat: number;
  lon: number;
  count: number;
}

/** data.police.uk street-level crime within the radius for the latest published month. */
export interface AroundCrime {
  /** "2026-08" — police.uk publishes about two months behind. */
  month: string;
  total: number;
  byCategory: { category: string; count: number }[];
  streets: CrimeStreet[];
  /** [lon, lat] per incident, for the heat layer. */
  points: [number, number][];
}

/** Environment Agency tide gauge nearest the point (Tower Pier for the City). */
export interface AroundTide {
  station: string;
  lat: number;
  lon: number;
  /** Metres above Ordnance Datum. */
  levelM: number;
  trend: "rising" | "falling" | "steady";
  at: number;
}

export interface AroundAir {
  /** European AQI, 0–100+ (Open-Meteo). */
  eaqi: number;
  band: "good" | "fair" | "moderate" | "poor" | "very poor";
  pm25: number;
  pm10: number;
  no2: number;
  at: number;
}

/** An HD public live stream of London (services/vision/cameras/london_live.json), embed-only. */
export interface AroundLiveCam {
  id: string;
  name: string;
  area: string;
  lat: number;
  lon: number;
  precision: "exact" | "approx";
  subjects: string[];
  /** YouTube embed URL; the stream is viewed in an iframe, never downloaded. */
  embedUrl: string;
  page: string;
  distanceM: number;
}
