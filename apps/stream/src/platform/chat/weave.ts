const TRACE_URL = "https://trace.wandb.ai";
const TRACE_TIMEOUT_MS = 3_000;

/** `team/project` the traces are filed under; null (tracing off) unless the key and both names are set. */
function traceProject(): string | null {
  const team = process.env.WANDB_TEAM;
  const project = process.env.WANDB_PROJECT;
  return process.env.WANDB_API_KEY && team && project ? `${team}/${project}` : null;
}

export interface TraceCall {
  id: string;
  traceId: string;
}

function post(path: string, body: unknown): Promise<void> {
  return fetch(`${TRACE_URL}${path}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Basic ${Buffer.from(`api:${process.env.WANDB_API_KEY}`).toString("base64")}`,
    },
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(TRACE_TIMEOUT_MS),
  }).then(() => undefined);
}

/**
 * Run `fn` as one Weave call. The `weave` SDK is Python/browser-first, so the call is written to the
 * W&B Trace server directly: start and end are posted fire-and-forget and never delay or fail `fn`.
 * Op names follow services/predict (`chat.persona_batch`, `chat.scene_describe`, ...). Pass the parent
 * call to nest a child under it in the Weave UI.
 */
export async function traced<T>(
  op: string,
  inputs: Record<string, unknown>,
  fn: (call: TraceCall | null) => Promise<T>,
  parent?: TraceCall | null,
  summarize: (out: T) => unknown = (out) => out,
): Promise<T> {
  const project = traceProject();
  if (!project) return fn(null);

  const call: TraceCall = { id: crypto.randomUUID(), traceId: parent?.traceId ?? crypto.randomUUID() };
  const started = post("/call/start", {
    start: {
      project_id: project,
      id: call.id,
      op_name: op,
      trace_id: call.traceId,
      parent_id: parent?.id ?? null,
      started_at: new Date().toISOString(),
      attributes: {},
      inputs,
    },
  }).catch(() => undefined);
  const end = (fields: Record<string, unknown>) =>
    started
      .then(() =>
        post("/call/end", {
          end: { project_id: project, id: call.id, ended_at: new Date().toISOString(), summary: {}, ...fields },
        }),
      )
      .catch(() => undefined);

  try {
    const out = await fn(call);
    void end({ output: summarize(out) });
    return out;
  } catch (err) {
    void end({ exception: err instanceof Error ? err.message : String(err) });
    throw err;
  }
}
