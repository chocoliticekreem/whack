import { complete, repairJson, wandbConfig } from "@/app/api/chat/scene/wandb";
import { traced } from "@/platform/chat/weave";
import { CLASSES, cleanQuery, COLORS, keywordQuery, type CamQuery } from "@/twin/search/match";

/**
 * Turns a free-text camera search ("a red bus", "crowd outside the station") into a CamQuery with
 * Nemotron on W&B Inference, traced in Weave as `twin.cam_search`. Matching itself runs in the browser
 * over the live YOLO tracks. Falls back to the keyword parser when W&B is unset, slow or unparseable.
 */

const TIMEOUT_MS = 6_000;

const SYSTEM = `You turn a search over live London traffic cameras into a JSON filter. The cameras only know what YOLO tracks: object class, a rough colour, and how many.
Reply with one JSON object and nothing else:
{"classes": [...], "colors": [...], "minCount": n, "label": "..."}
- classes: any of ${JSON.stringify(CLASSES)}; empty for anything. Vans and lorries are "truck"; cyclists are "bicycle".
- colors: any of ${JSON.stringify(COLORS)}; empty for any colour. Silver is "grey".
- minCount: how many matching objects one camera must show; 1 unless the question implies more ("a crowd" = 5 people, "heavy traffic" = 10 vehicles, "three buses" = 3).
- label: a short plural noun phrase for the results, e.g. "red buses".`;

export async function POST(request: Request) {
  const body = (await request.json().catch(() => ({}))) as { q?: unknown };
  const q = typeof body.q === "string" ? body.q.trim().slice(0, 200) : "";
  if (!q) return Response.json({ error: "Send { q }." }, { status: 400 });
  const fallback = keywordQuery(q);
  const cfg = wandbConfig();
  if (!cfg) return Response.json({ query: fallback, source: "keywords" });
  try {
    const query = await traced<CamQuery>("twin.cam_search", { q, model: cfg.textModel }, async () => {
      const raw = await complete(
        cfg,
        cfg.textModel,
        [
          { role: "system", content: SYSTEM },
          { role: "user", content: q },
        ],
        { maxTokens: 160, timeoutMs: TIMEOUT_MS, temperature: 0 },
      );
      const parsed = repairJson<Partial<Record<keyof CamQuery, unknown>>>(raw);
      if (!parsed) throw new Error("unparseable filter");
      return cleanQuery(parsed, fallback);
    });
    return Response.json({ query, source: cfg.textModel });
  } catch {
    return Response.json({ query: fallback, source: "keywords" });
  }
}
