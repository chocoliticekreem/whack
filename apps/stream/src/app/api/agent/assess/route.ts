import type { NextRequest } from "next/server";
import type { AroundAir, AroundCam, AroundCrime, AroundDisruption, AroundStation, CamReading } from "@/platform/around/types";
import type { AdviceLevel, AssessRequest, Assessment } from "@/platform/agent/types";
import type { FieldResponse } from "@/twin/field/types";
import { AGENT_URL } from "@/platform/agent/agentUrl";

const WANDB_BASE_URL = process.env.WANDB_BASE_URL ?? "https://api.inference.wandb.ai/v1";
const WANDB_MODEL = process.env.WANDB_MODEL ?? "nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B";
const HOT_PEDS = 6;

type ClusterReply = Omit<Assessment, "source" | "liveCams" | "at">;

async function getJson<T>(url: string, fallback: T): Promise<T> {
  try {
    const res = await fetch(url, { signal: AbortSignal.timeout(6000), cache: "no-store" });
    if (!res.ok) return fallback;
    return (await res.json()) as T;
  } catch {
    return fallback;
  }
}

async function askCluster(body: AssessRequest, client: AbortSignal): Promise<ClusterReply | null> {
  try {
    const res = await fetch(`${AGENT_URL}/assess`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
      // The cluster agent runs a VSS search and an LLM call; 20–40 s is normal.
      // Stop the cluster call too when the browser gives up, so abandoned requests don't pile up on the agent.
      signal: AbortSignal.any([client, AbortSignal.timeout(45_000)]),
      cache: "no-store",
    });
    if (!res.ok) return null;
    return (await res.json()) as ClusterReply;
  } catch {
    return null;
  }
}

interface LocalSignals {
  cams: { name: string; people: number; vehicles: number; distanceM: number }[];
  closures: string[];
  hotspots: string[];
  /** TfL live station crowding as a share of a normal day, plus any line not on Good Service. */
  stations: { name: string; distanceM: number; crowdingShareOfNormal: number | null; lineProblems: string[] }[];
  /** police.uk street-level crime for the latest published month within 600 m. */
  crimeLastMonth: { month: string; total: number; topCategories: string[]; topStreets: string[] } | null;
  airQuality: { europeanAqi: number; band: string } | null;
}

const NO_SIGNALS: LocalSignals = {
  cams: [],
  closures: [],
  hotspots: [],
  stations: [],
  crimeLastMonth: null,
  airQuality: null,
};

async function localSignals(origin: string, loc: { lat: number; lon: number }): Promise<LocalSignals> {
  const q = `lat=${loc.lat}&lon=${loc.lon}`;
  const [{ cams }, disruptions, field, { stations }, crime, air] = await Promise.all([
    getJson<{ cams: AroundCam[] }>(`${origin}/api/around/cams?${q}&radius=500`, { cams: [] }),
    getJson<{ items: AroundDisruption[] }>(`${origin}/api/around/disruptions?${q}&radius=400`, { items: [] }),
    getJson<FieldResponse | null>(`${origin}/api/twin/field`, null),
    getJson<{ stations: AroundStation[] }>(`${origin}/api/around/stations?${q}&radius=600`, { stations: [] }),
    getJson<AroundCrime | null>(`${origin}/api/around/crime?${q}&radius=600`, null),
    getJson<AroundAir | null>(`${origin}/api/around/air?${q}`, null),
  ]);
  const near = cams.slice(0, 4);
  const { readings } = near.length
    ? await getJson<{ readings: CamReading[] }>(`${origin}/api/around/counts?ids=${near.map((c) => c.id).join(",")}`, {
        readings: [],
      })
    : { readings: [] };
  const byId = new Map((readings ?? []).map((r) => [r.camId, r]));
  const hotspots = new Set<string>();
  if (field) {
    for (const s of field.segments) {
      if (s.peds < HOT_PEDS) continue;
      const mx = (s.a[0] + s.b[0]) / 2;
      const mz = (s.a[1] + s.b[1]) / 2;
      const anchor = field.anchors.find((a) => Math.hypot(a.x - mx, a.z - mz) < 100);
      if (anchor) hotspots.add(anchor.name);
      if (hotspots.size >= 5) break;
    }
  }
  return {
    cams: near
      .filter((c) => byId.has(c.id))
      .map((c) => ({ name: c.name, people: byId.get(c.id)!.people, vehicles: byId.get(c.id)!.vehicles, distanceM: c.distanceM })),
    closures: (disruptions.items ?? []).filter((d) => /closed/i.test(d.closure)).map((d) => d.street),
    hotspots: [...hotspots],
    stations: (stations ?? []).slice(0, 3).map((s) => ({
      name: s.name,
      distanceM: s.distanceM,
      crowdingShareOfNormal: s.crowding,
      lineProblems: s.lines.filter((l) => l.status !== "Good Service").map((l) => `${l.name}: ${l.status}`),
    })),
    crimeLastMonth:
      crime && crime.month
        ? {
            month: crime.month,
            total: crime.total,
            topCategories: crime.byCategory.slice(0, 3).map((c) => `${c.category} ${c.count}`),
            topStreets: crime.streets.slice(0, 3).map((s) => `${s.name} ${s.count}`),
          }
        : null,
    airQuality: air ? { europeanAqi: air.eaqi, band: air.band } : null,
  };
}

function levelFrom(raw: unknown): AdviceLevel {
  return raw === "danger" || raw === "busy" ? raw : "calm";
}

async function askLocalLlm(body: AssessRequest, signals: LocalSignals): Promise<ClusterReply | { error: string }> {
  const key = process.env.WANDB_API_KEY;
  if (!key) return { error: "WANDB_API_KEY not set" };
  const system =
    "You advise an IRL livestreamer walking through London. Answer ONLY as JSON: " +
    '{"level":"calm"|"busy"|"danger","advice":string (1-2 sentences, second person, concrete),"reasons":string[] (max 3, each cites a number from the signals)}. ' +
    "Levels: calm = under 8 people on the nearest camera and no closures; busy = 8-19 people or a closure nearby; danger = 20+ people, a crowd forming, or a serious incident. " +
    "Station crowding over 1.2 means a busier-than-normal station. Crime figures are last month's, context not a live threat. Never invent signals.";
  const user = JSON.stringify({
    question: body.question,
    location: body.location,
    streamerCamera: body.yolo,
    liveCameras: signals.cams,
    roadClosures: signals.closures,
    estimatedCrowdHotspots: signals.hotspots,
    stations: signals.stations,
    crimeLastMonth: signals.crimeLastMonth,
    airQuality: signals.airQuality,
    time: new Date().toLocaleString("en-GB", { timeZone: "Europe/London" }),
  });
  try {
    const res = await fetch(`${WANDB_BASE_URL}/chat/completions`, {
      method: "POST",
      headers: { "content-type": "application/json", authorization: `Bearer ${key}` },
      body: JSON.stringify({
        model: WANDB_MODEL,
        temperature: 0.2,
        max_tokens: 1500,
        // Nemotron otherwise spends the whole budget reasoning and returns empty content.
        chat_template_kwargs: { enable_thinking: false },
        messages: [
          { role: "system", content: system },
          { role: "user", content: user },
        ],
      }),
      signal: AbortSignal.timeout(20_000),
    });
    if (!res.ok) return { error: `W&B Inference HTTP ${res.status}: ${(await res.text()).slice(0, 200)}` };
    const data = (await res.json()) as {
      choices?: { message?: { content?: string | null; reasoning_content?: string | null } }[];
    };
    const message = data.choices?.[0]?.message;
    const text = message?.content || message?.reasoning_content || "";
    const start = text.indexOf("{");
    const end = text.lastIndexOf("}");
    if (start < 0 || end < start) {
      return { error: `W&B Inference returned no JSON: ${JSON.stringify(data).slice(0, 300)}` };
    }
    const parsed = JSON.parse(text.slice(start, end + 1)) as Partial<ClusterReply>;
    if (typeof parsed.advice !== "string") return { error: "W&B Inference returned no advice" };
    return {
      level: levelFrom(parsed.level),
      advice: parsed.advice,
      reasons: Array.isArray(parsed.reasons) ? parsed.reasons.filter((r): r is string => typeof r === "string") : [],
      evidence: [],
    };
  } catch (e) {
    return { error: `W&B Inference failed: ${(e as Error).message}` };
  }
}

export async function POST(request: NextRequest) {
  const body = (await request.json().catch(() => null)) as AssessRequest | null;
  if (!body || typeof body.question !== "string") {
    return Response.json({ error: "question is required" }, { status: 400 });
  }
  const origin = request.nextUrl.origin;
  const loc = body.location ?? null;
  const [cluster, signals] = await Promise.all([
    askCluster(body, request.signal),
    loc ? localSignals(origin, loc) : Promise.resolve<LocalSignals>(NO_SIGNALS),
  ]);
  const liveCams = signals.cams.length || (body.yolo ? 1 : 0);
  if (cluster) {
    const out: Assessment = { ...cluster, source: "cluster-agent", liveCams, at: Date.now() };
    return Response.json(out);
  }
  const local = await askLocalLlm(body, signals);
  if (!("error" in local)) {
    const out: Assessment = { ...local, source: "local-llm", liveCams, at: Date.now() };
    return Response.json(out);
  }
  return Response.json({ error: `Cluster agent at ${AGENT_URL} unreachable. ${local.error}` }, { status: 503 });
}
