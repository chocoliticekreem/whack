import type { NextRequest } from "next/server";
import { compose, createMemory, EVENT_LINES } from "@/agents/chat-crowd/engine";
import { ARCHETYPE_VOICE } from "@/agents/chat-crowd/personas";
import {
  normalizePlace,
  type CrowdEvent,
  type Place,
  type SceneDescription,
  type SceneLine,
  type SceneRequest,
  type SceneResponse,
} from "@/agents/chat-crowd/scene";
import type { Archetype } from "@/lib/types";
import { traced, type TraceCall } from "@/platform/chat/weave";
import { grabFrame } from "./frame";
import { cleanLines, MAX_LINE_CHARS } from "./validate";
import { complete, repairJson, wandbConfig, WandbError, type WandbConfig } from "./wandb";

const MAX_FRAME_BYTES = 400_000;
/** Every external call (clip grab, vision, text) gets its own budget, so a request never hangs. */
const VISION_TIMEOUT_MS = 8_000;
const TEXT_TIMEOUT_MS = 8_000;
const DEFAULT_COUNT = 6;
const MIN_COUNT = 3;
const MAX_COUNT = 10;
const MAX_PERSONAS = 14;
const MAX_RECENT = 12;

const EVENTS: CrowdEvent[] = ["ambient", "streamer_message", "location_leaked", "mirror"];
const ARCHETYPES = Object.keys(ARCHETYPE_VOICE) as Archetype[];

const VISION_PROMPT = `You describe a single frame from a live IRL stream for a chat-simulation. Reply with STRICT JSON only, no markdown, no prose:
{"scene": "<one sentence, max 25 words: where this looks like and what is happening>",
 "notable": ["<3-6 concrete things a viewer would react to: landmarks, weather, traffic, queues, buses, light, signage>"],
 "mood": "<one or two words>",
 "safety_flags": ["<only real hazards visible, e.g. collision, fire, crowd crush; usually empty>"]}
Rules: never describe or identify people's faces, identities, clothing brands or number plates. Describe the place, weather, movement and objects.`;

const TEXT_SYSTEM = `You write live chat for a London IRL stream: a crowd of distinct viewer personas reacting in real time.
Style: UK Gen-Z, mostly lowercase, short (2 to 12 words), slangy but readable. At most two lines in the batch may contain an emoji. No hashtags, links or @mentions.
Keep it PG: no slurs, hate, sexual content, swearing, threats or violence. Never name a real person (streamers, celebrities, politicians); the streamer is just "the streamer". Never give addresses or street numbers; only name the place you are given. Never say you are an AI or that this is a simulation of chat.
Each line comes from a different username and sounds like that persona's archetype. Make every line a different idea, specific to what is on screen, the streamer's message or the event, and never repeat or paraphrase the recent chat.
Reply with STRICT JSON only: {"lines":[{"username":"<one of the given usernames>","text":"<max ${MAX_LINE_CHARS} chars>","locationSignal":<true only if the line says they are coming to the streamer or know exactly where the streamer is>}]}`;

const EVENT_BRIEF: Record<CrowdEvent, string> = {
  ambient: "Nothing special just happened. Chat reacts to the place, the weather and light, traffic and crowds, and what is on screen, with the odd joke or question for the streamer.",
  streamer_message:
    "The streamer just typed in chat. Most lines answer or react to that message directly; the rest can be side chatter.",
  location_leaked:
    "The streamer JUST told chat exactly where they are. Opportunists hype it and plan to come down; locals warn about crowding, narrow pavements and transport; superfans worry about the streamer's safety; haters say it is a bad idea or clout chasing; the questioner asks how many are coming or where to meet; the mod asks people to stay safe and not pile on.",
  mirror:
    "The streamer just opened the Mirror: a 3D digital twin of this exact area, used to rehearse what would happen if they told chat where they are. Chat asks whether that is the twin, is impressed or sceptical, and wonders what it predicts. Locals can say whether the layout looks right.",
};

function textPrompt(body: SceneRequest, place: Place, scene: SceneDescription | null, count: number) {
  const archetypes = [...new Set(body.personas.map((p) => p.archetype))];
  const recent = body.recent.map((m) => `${m.username}: ${m.text}`).join("\n") || "(nothing yet)";
  const distances = body.leaked
    ? `Viewers may say roughly how far away they are in plain words, like "about 1.5 km from ${place.name}"; never write distances glued together like "1560m from".`
    : "Nobody knows exactly where the streamer is yet: no distances, and nobody says they are on their way. Guess the place only if the scene shows it.";
  return [
    `Place: ${place.name}${place.area ? ` (${place.area})` : ""}. ${body.viewers} watching.`,
    `Event: ${EVENT_BRIEF[body.event]}`,
    body.streamerMessage ? `The streamer's message: "${body.streamerMessage}"` : null,
    body.surroundings
      ? `Surroundings advice the streamer sees (${body.surroundings.level}): ${body.surroundings.summary}. Locals may echo it in their own words.`
      : null,
    scene ? `On screen: ${scene.scene}. Notable: ${scene.notable.join("; ") || "nothing specific"}. Mood: ${scene.mood}.` : null,
    distances,
    `Recent chat, oldest first (do not repeat):\n${recent}`,
    `Archetype voices:\n${archetypes.map((a) => `- ${a}: ${ARCHETYPE_VOICE[a]}`).join("\n")}`,
    `Write ${count} lines. Use ONLY these usernames, each at most once: ${body.personas.map((p) => `${p.username} (${p.archetype})`).join(", ")}`,
  ]
    .filter(Boolean)
    .join("\n");
}

function badRequest(message: string) {
  return Response.json({ error: message }, { status: 400 });
}

function text(value: unknown, max: number): string {
  return typeof value === "string" ? value.replace(/\s+/g, " ").trim().slice(0, max) : "";
}

/** Validate and clamp the request body; null when it cannot be used. */
function parseBody(raw: unknown): SceneRequest | null {
  if (typeof raw !== "object" || raw === null) return null;
  const b = raw as Partial<SceneRequest>;
  const name = text(b.location?.name, 80);
  const personas = (Array.isArray(b.personas) ? b.personas : [])
    .filter((p) => typeof p?.username === "string" && ARCHETYPES.includes(p.archetype))
    .slice(0, MAX_PERSONAS)
    .map((p) => ({ username: p.username.slice(0, 40), archetype: p.archetype }));
  if (!name || personas.length === 0) return null;
  const level = b.surroundings?.level;
  return {
    frame: typeof b.frame === "string" ? b.frame : undefined,
    videoUrl: typeof b.videoUrl === "string" ? b.videoUrl : undefined,
    location: { name, area: text(b.location?.area, 40) },
    viewers: Number.isFinite(b.viewers) ? Math.max(0, Math.round(b.viewers as number)) : 0,
    event: EVENTS.includes(b.event as CrowdEvent) ? (b.event as CrowdEvent) : "ambient",
    leaked: b.leaked === true,
    streamerMessage: text(b.streamerMessage, 200) || undefined,
    surroundings:
      b.surroundings && (level === "calm" || level === "busy" || level === "danger")
        ? { summary: text(b.surroundings.summary, 240), level }
        : undefined,
    recent: (Array.isArray(b.recent) ? b.recent : [])
      .filter((m) => typeof m?.username === "string" && typeof m?.text === "string")
      .slice(-MAX_RECENT)
      .map((m) => ({ username: m.username.slice(0, 40), text: text(m.text, 160) })),
    personas,
    count: Math.min(MAX_COUNT, Math.max(MIN_COUNT, Math.round(b.count ?? DEFAULT_COUNT))),
  };
}

function decodeFrame(frame: string): Buffer | null {
  const base64 = frame.replace(/^data:image\/\w+;base64,/, "");
  if (!/^[A-Za-z0-9+/=\s]+$/.test(base64)) return null;
  const buf = Buffer.from(base64, "base64");
  return buf.length > 0 && buf.length <= MAX_FRAME_BYTES ? buf : null;
}

/** The frame to look at: the one the browser sent, else one grabbed from the clip; null means text-only. */
async function frameFor(body: SceneRequest): Promise<Buffer | null> {
  try {
    if (body.frame) return decodeFrame(body.frame);
    if (body.videoUrl) return await grabFrame(body.videoUrl);
  } catch {
    return null;
  }
  return null;
}

async function describe(cfg: WandbConfig, jpeg: Buffer): Promise<SceneDescription> {
  const raw = await complete(
    cfg,
    cfg.visionModel,
    [
      {
        role: "user",
        content: [
          { type: "text", text: VISION_PROMPT },
          { type: "image_url", image_url: { url: `data:image/jpeg;base64,${jpeg.toString("base64")}` } },
        ],
      },
    ],
    { maxTokens: 400, timeoutMs: VISION_TIMEOUT_MS, temperature: 0.2 },
  );
  const parsed = repairJson<Partial<SceneDescription>>(raw);
  if (!parsed || typeof parsed.scene !== "string") {
    throw new WandbError(`vision model returned non-JSON: ${raw.slice(0, 120)}`, 502);
  }
  const strings = (v: unknown) =>
    Array.isArray(v) ? v.filter((x): x is string => typeof x === "string").map((s) => s.trim()).slice(0, 8) : [];
  return {
    scene: parsed.scene.trim().slice(0, 200),
    notable: strings(parsed.notable),
    mood: typeof parsed.mood === "string" ? parsed.mood.trim().slice(0, 40) : "neutral",
    safety_flags: strings(parsed.safety_flags),
  };
}

/**
 * Dev-only stand-in for the model (`?mock=1`, or `?mock=dirty` to include lines the validator must drop),
 * so the route, validation and client can be exercised without a W&B key.
 */
function mockReply(body: SceneRequest, place: Place, mode: string): string {
  const memory = createMemory();
  const ctx = {
    streamer: "streamer",
    location: { name: place.name, area: place.area },
    locationRevealedAt: body.leaked ? Date.now() : null,
  };
  const lines = body.personas.map((p) => ({
    username: p.username,
    text: compose(memory, ctx, p.archetype, EVENT_LINES[body.event], Math.random) ?? "same here",
    locationSignal: body.event === "location_leaked" && p.archetype === "opportunist",
  }));
  if (mode !== "dirty") return JSON.stringify({ lines });
  const first = body.personas[0].username;
  return JSON.stringify({
    lines: [
      { username: "not_a_persona", text: "who dis" },
      { username: first, text: `i'm 1560m from ${place.name}` },
      { username: first, text: "kai cenat would never" },
      { username: first, text: "x".repeat(MAX_LINE_CHARS + 20) },
      { username: first, text: "check https://example.com now" },
      { username: first, text: "that was shit" },
      ...lines,
      { username: first, text: lines[0].text },
    ],
  });
}

export async function POST(request: NextRequest) {
  const mock = process.env.NODE_ENV === "production" ? null : request.nextUrl.searchParams.get("mock");
  const configured = wandbConfig();
  if (!configured && !mock) return Response.json({ error: "WANDB_API_KEY not configured" }, { status: 501 });
  const cfg = mock ? null : configured;

  const body = parseBody(await request.json().catch(() => null));
  if (!body) return badRequest("location and personas are required");
  const place = normalizePlace(body.location.name, body.location.area);
  const count = body.count ?? DEFAULT_COUNT;
  const textModel = cfg?.textModel ?? "mock";

  try {
    const result = await traced(
      "chat.persona_batch",
      {
        event: body.event,
        place,
        viewers: body.viewers,
        leaked: body.leaked,
        streamer_message: body.streamerMessage ?? null,
        surroundings: body.surroundings ?? null,
        recent: body.recent,
        personas: body.personas,
        count,
        has_frame: Boolean(body.frame || body.videoUrl),
      },
      async (call: TraceCall | null) => {
        const t0 = Date.now();
        const jpeg = await frameFor(body);
        const t1 = Date.now();

        let scene: SceneDescription | null = null;
        if (jpeg && cfg) {
          scene = await traced("chat.scene_describe", { model: cfg.visionModel, frame_bytes: jpeg.length }, () => describe(cfg, jpeg), call).catch(
            () => null,
          );
        } else if (jpeg) {
          scene = { scene: "mock frame", notable: [], mood: "neutral", safety_flags: [] };
        }
        const t2 = Date.now();

        const prompt = textPrompt(body, place, scene, count);
        const raw = await traced(
          "chat.write_lines",
          { model: textModel, system: TEXT_SYSTEM, prompt },
          () =>
            cfg
              ? complete(
                  cfg,
                  cfg.textModel,
                  [
                    { role: "system", content: TEXT_SYSTEM },
                    { role: "user", content: prompt },
                  ],
                  { maxTokens: 900, timeoutMs: TEXT_TIMEOUT_MS, temperature: 0.9 },
                )
              : Promise.resolve(mockReply(body, place, mock ?? "1")),
          call,
        );
        const lines: SceneLine[] = cleanLines(raw, {
          allowed: new Set(body.personas.map((p) => p.username)),
          recent: body.recent.map((m) => m.text),
          leaked: body.leaked,
          max: count,
        });
        if (lines.length === 0) throw new WandbError(`text model returned no usable lines: ${raw.slice(0, 200)}`, 502);
        const t3 = Date.now();

        const out: SceneResponse = {
          scene,
          lines,
          backend: cfg ? "wandb" : "mock",
          models: { vision: scene && cfg ? cfg.visionModel : null, text: textModel },
          timings: { frameMs: t1 - t0, visionMs: t2 - t1, textMs: t3 - t2 },
        };
        return out;
      },
      null,
      (out) => ({ lines: out.lines, scene: out.scene?.scene ?? null, timings: out.timings }),
    );
    return Response.json(result, { headers: { "Cache-Control": "no-store" } });
  } catch (err) {
    const status = err instanceof WandbError ? (err.status === 429 ? 429 : 502) : 504;
    const message = err instanceof Error ? err.message : String(err);
    return Response.json({ error: message }, { status });
  }
}
