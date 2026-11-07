const DEFAULT_VISION_MODEL = "nvidia/NVIDIA-Nemotron-3.5-Super-VL-120B-A12B";
const DEFAULT_TEXT_MODEL = "nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B";

export interface WandbConfig {
  apiKey: string;
  baseUrl: string;
  visionModel: string;
  textModel: string;
  /** `team/project`; sent as OpenAI-Project so W&B attributes usage (the `weave` SDK is not installed). */
  project: string | null;
}

export function wandbConfig(): WandbConfig | null {
  const apiKey = process.env.WANDB_API_KEY;
  if (!apiKey) return null;
  const team = process.env.WANDB_TEAM;
  const proj = process.env.WANDB_PROJECT;
  return {
    apiKey,
    baseUrl: (process.env.WANDB_BASE_URL ?? "https://api.inference.wandb.ai/v1").replace(/\/$/, ""),
    visionModel: process.env.WANDB_VISION_MODEL ?? DEFAULT_VISION_MODEL,
    textModel: process.env.WANDB_MODEL ?? DEFAULT_TEXT_MODEL,
    project: team && proj ? `${team}/${proj}` : null,
  };
}

type Content = string | ({ type: "text"; text: string } | { type: "image_url"; image_url: { url: string } })[];

export class WandbError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

/** One non-streaming chat completion. Returns the assistant text (content, or reasoning if content is empty). */
export async function complete(
  cfg: WandbConfig,
  model: string,
  messages: { role: "system" | "user"; content: Content }[],
  opts: { maxTokens: number; timeoutMs: number; temperature?: number },
): Promise<string> {
  const headers: Record<string, string> = {
    Authorization: `Bearer ${cfg.apiKey}`,
    "Content-Type": "application/json",
  };
  if (cfg.project) headers["OpenAI-Project"] = cfg.project;

  const res = await fetch(`${cfg.baseUrl}/chat/completions`, {
    method: "POST",
    headers,
    signal: AbortSignal.timeout(opts.timeoutMs),
    body: JSON.stringify({
      model,
      messages,
      max_tokens: opts.maxTokens,
      temperature: opts.temperature ?? 0.4,
      // Nemotron thinks by default and spends the whole token budget on it.
      chat_template_kwargs: { enable_thinking: false },
    }),
  });
  if (!res.ok) {
    const body = (await res.text()).slice(0, 300);
    throw new WandbError(`W&B ${model} responded ${res.status}: ${body}`, res.status);
  }
  const data = (await res.json()) as {
    choices?: { message?: { content?: string | null; reasoning?: string | null } }[];
  };
  const msg = data.choices?.[0]?.message;
  const text = msg?.content?.trim() || msg?.reasoning?.trim() || "";
  if (!text) throw new WandbError(`W&B ${model} returned an empty message`, 502);
  return text;
}

/** Pull the first JSON object out of model text, tolerating fences, prose and trailing commas. */
export function repairJson<T>(raw: string): T | null {
  const unfenced = raw.replace(/```(?:json)?/gi, "");
  const start = unfenced.indexOf("{");
  const end = unfenced.lastIndexOf("}");
  if (start < 0 || end <= start) return null;
  const slice = unfenced.slice(start, end + 1);
  const fixes: ((s: string) => string)[] = [
    (s) => s,
    (s) => s.replace(/,\s*([}\]])/g, "$1"),
    (s) => s.replace(/"(\w+):(?=\s*(?:true|false|null|-?\d|"))/g, '"$1":'),
    (s) => s.replace(/[‘’]/g, "'").replace(/[“”]/g, '"'),
  ];
  let candidate = slice;
  for (const fix of fixes) {
    candidate = fix(candidate);
    try {
      return JSON.parse(candidate) as T;
    } catch {
      continue;
    }
  }
  return null;
}

/** Last resort for a list the model mangled: parse each `{...}` item on its own and keep the good ones. */
export function salvageObjects(raw: string): Record<string, unknown>[] {
  const out: Record<string, unknown>[] = [];
  for (const m of raw.matchAll(/\{[^{}]*\}/g)) {
    const obj = repairJson<Record<string, unknown>>(m[0]);
    if (obj) out.push(obj);
  }
  return out;
}
