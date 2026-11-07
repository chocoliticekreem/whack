import { readFile, writeFile } from "node:fs/promises";
import path from "node:path";

/**
 * Saves a camera pose aligned by hand in the compare view into twin/live/calibration.json, the same file
 * services/vision/calibrate.py writes. Dev only: it edits a source file, which then hot-reloads.
 */
const FILE = path.join(process.cwd(), "src", "twin", "live", "calibration.json");

interface Pose {
  headingDeg: number;
  pitchDeg: number;
  heightM: number;
  hfovDeg: number;
  aspect: number;
}

const FIELDS = ["headingDeg", "pitchDeg", "heightM", "hfovDeg", "aspect"] as const;

export async function POST(request: Request) {
  if (process.env.NODE_ENV === "production") {
    return Response.json({ error: "Saving poses only works in dev (it writes a source file)." }, { status: 403 });
  }
  const body = (await request.json()) as { camId?: string; pose?: Partial<Pose> };
  const pose = body.pose ?? {};
  if (!body.camId?.startsWith("JamCams_") || FIELDS.some((k) => !Number.isFinite(pose[k]))) {
    return Response.json({ error: "Send { camId, pose: { headingDeg, pitchDeg, heightM, hfovDeg, aspect } }." }, { status: 400 });
  }
  const all = JSON.parse(await readFile(FILE, "utf8")) as Record<string, Record<string, unknown>>;
  const before = all[body.camId] ?? {};
  const origin = String(before.source ?? "the default pose").replace(/^aligned by hand in the compare view, starting from: /, "");
  all[body.camId] = {
    ...before,
    ...Object.fromEntries(FIELDS.map((k) => [k, Math.round((pose[k] as number) * 100) / 100])),
    source: `aligned by hand in the compare view, starting from: ${origin}`,
    at: new Date().toISOString().slice(0, 19) + "+00:00",
  };
  await writeFile(FILE, JSON.stringify(all, null, 2) + "\n");
  return Response.json({ ok: true, pose: all[body.camId] });
}
