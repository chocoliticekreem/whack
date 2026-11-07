"use client";

import { useState, type CSSProperties } from "react";
import { useCompare } from "../compare/store";
import { useLive } from "../live/store";
import { keywordQuery, searchCams, type CamMatch, type CamQuery } from "./match";

const pill: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 4,
  padding: 4,
  borderRadius: 999,
  border: "1px solid var(--border)",
  background: "rgba(0,0,0,0.5)",
  backdropFilter: "blur(12px)",
  pointerEvents: "auto",
};

const SUGGESTIONS = ["red bus", "white van", "crowd", "cyclists", "heavy traffic"];

interface Result {
  query: CamQuery;
  source: string;
  matches: CamMatch[];
}

/**
 * "Search the cameras": what is happening on which JamCam right now. Nemotron turns the question into a
 * class / colour / count filter; every camera's live YOLO tracks are matched against it, and picking a
 * result opens that camera's compare view.
 */
export function CamSearch() {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<Result | null>(null);
  const open = useCompare((s) => s.open);

  const run = async (q: string) => {
    if (!q.trim()) return;
    setText(q);
    setBusy(true);
    let query = keywordQuery(q);
    let source = "keywords";
    try {
      const res = await fetch("/api/twin/search", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ q }),
        signal: AbortSignal.timeout(8_000),
      });
      if (res.ok) ({ query, source } = (await res.json()) as { query: CamQuery; source: string });
    } catch {
      // Keyword parse already in hand.
    }
    const { cams, clips } = useLive.getState();
    setResult({ query, source, matches: searchCams(query, cams, clips) });
    setBusy(false);
  };

  return (
    <div style={{ position: "relative" }}>
      <form
        style={pill}
        onSubmit={(e) => {
          e.preventDefault();
          void run(text);
        }}
      >
        <input
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Search the cameras: red bus, crowd…"
          aria-label="Search the live cameras"
          style={{
            width: 220,
            maxWidth: "52vw",
            padding: "6px 12px",
            borderRadius: 999,
            border: "none",
            outline: "none",
            background: "transparent",
            color: "var(--text)",
            fontSize: 12,
            fontFamily: "inherit",
          }}
        />
        {result && (
          <button
            type="button"
            aria-label="Clear search"
            onClick={() => {
              setResult(null);
              setText("");
            }}
            style={{ border: "none", background: "transparent", color: "var(--muted)", cursor: "pointer", padding: "0 8px", fontSize: 14 }}
          >
            ×
          </button>
        )}
      </form>
      {(busy || result) && (
        <div
          style={{
            position: "absolute",
            bottom: "calc(100% + 8px)",
            left: 0,
            zIndex: 40,
            minWidth: 280,
            maxWidth: "calc(100vw - 24px)",
            padding: 6,
            borderRadius: 14,
            border: "1px solid var(--border)",
            background: "rgba(12,12,16,0.9)",
            backdropFilter: "blur(14px)",
            display: "grid",
            gap: 2,
            pointerEvents: "auto",
          }}
        >
          {busy ? (
            <div style={{ padding: "8px 10px", fontSize: 12, color: "var(--muted)" }}>Reading every camera…</div>
          ) : result && (
            <>
              <div style={{ padding: "6px 10px 4px", fontSize: 11, color: "var(--muted)" }}>
                {result.matches.length
                  ? `${result.query.label} on ${result.matches.length} camera${result.matches.length === 1 ? "" : "s"}`
                  : `No camera shows ${result.query.label} right now`}
                {" · "}
                {result.source === "keywords" ? "keyword match" : "parsed by Nemotron on W&B"}
              </div>
              {result.matches.map((m) => (
                <button
                  key={m.cam.id}
                  type="button"
                  onClick={() => {
                    setResult(null);
                    open(m.cam.id);
                  }}
                  style={{
                    textAlign: "left",
                    padding: "7px 10px",
                    borderRadius: 9,
                    border: "none",
                    background: "transparent",
                    color: "var(--text)",
                    fontSize: 13,
                    cursor: "pointer",
                    fontFamily: "inherit",
                  }}
                  onMouseEnter={(e) => (e.currentTarget.style.background = "rgba(255,255,255,0.08)")}
                  onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
                >
                  {m.cam.name}
                  <span style={{ color: "var(--muted)", fontSize: 11 }}>
                    {" · "}
                    {m.count} tracked
                  </span>
                </button>
              ))}
              {!result.matches.length && (
                <div style={{ display: "flex", flexWrap: "wrap", gap: 4, padding: "2px 6px 6px" }}>
                  {SUGGESTIONS.map((s) => (
                    <button
                      key={s}
                      type="button"
                      onClick={() => void run(s)}
                      style={{
                        padding: "4px 10px",
                        borderRadius: 999,
                        border: "1px solid var(--border)",
                        background: "transparent",
                        color: "var(--text)",
                        fontSize: 11,
                        cursor: "pointer",
                        fontFamily: "inherit",
                      }}
                    >
                      {s}
                    </button>
                  ))}
                </div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}
