"use client";

import { useEffect } from "react";
import { useLive } from "../live/store";
import { RESCORE_MS, scoreLatest, usePredict } from "./store";
import { Button, Chip, card, label } from "./ui";

export function Score() {
  const baseline = usePredict((s) => s.baseline);
  const score = usePredict((s) => s.score);
  const scoring = usePredict((s) => s.scoring);
  const cams = useLive((s) => s.cams);
  const runId = baseline?.runId;

  useEffect(() => {
    if (!runId) return;
    const timer = setInterval(() => void scoreLatest(), RESCORE_MS);
    return () => clearInterval(timer);
  }, [runId]);

  if (!baseline) return null;
  const camName = (id: string) => cams.find((c) => c.id === id)?.name ?? id.replace("JamCams_", "cam ");
  const s = score?.summary;
  const zones = score?.zones ?? [];
  const zoneName = (id: string | null) => baseline.timeline[0].zones.find((z) => z.id === id)?.name ?? "outside zones";

  return (
    <div style={card}>
      <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
        <span style={{ fontWeight: 600, flex: 1 }}>Prediction vs reality</span>
        <Button small disabled={scoring} onClick={() => void scoreLatest()}>
          {scoring ? "Scoring…" : score ? "Re-score" : "Score"}
        </Button>
      </div>
      {!score && (
        <div style={label}>Compares the forecast step that has just arrived with the latest camera counts: direction (busier / quieter vs now) first, size second. Re-scores every 2 min.</div>
      )}
      {score?.error && <div style={{ fontSize: 12, color: "var(--warn)" }}>{score.error.replace(/; first due at \d+/, "")}</div>}
      {s && score?.compared === 0 && !score.error && (
        <div style={{ fontSize: 12, color: "var(--warn)" }}>No camera has a reading newer than the +{score.forecastMinute} min step yet; re-scores every 2 min.</div>
      )}
      {s && (score?.compared ?? 0) > 0 && (
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          <Chip color={s.zonesRight >= s.zonesWrong ? "var(--ok)" : "var(--warn)"}>
            direction right in {s.zonesRight} of {s.zonesRight + s.zonesWrong} zones
          </Chip>
          <span style={label}>
            MAE {s.meanAbsError === null || s.meanAbsError === undefined ? "—" : Math.round(s.meanAbsError)} people · +{score?.forecastMinute} min step ·{" "}
            {score?.compared} cameras
          </span>
        </div>
      )}
      {zones.length > 0 && (
        <div style={{ display: "grid", gap: 4, fontSize: 12, fontVariantNumeric: "tabular-nums" }}>
          <div style={{ display: "grid", gridTemplateColumns: "1fr auto auto auto", gap: "0 10px", color: "var(--muted)", fontSize: 11 }}>
            <span>Zone</span>
            <span style={{ textAlign: "right" }}>Now</span>
            <span style={{ textAlign: "right" }}>Predicted</span>
            <span style={{ textAlign: "right" }}>Actual</span>
          </div>
          {zones.map((z) => (
            <div key={z.zoneId ?? "none"} style={{ display: "grid", gridTemplateColumns: "1fr auto auto auto", gap: "0 10px", borderTop: "1px solid var(--border)", paddingTop: 4 }}>
              <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{zoneName(z.zoneId)}</span>
              <span style={{ textAlign: "right", color: "var(--muted)" }}>{Math.round(z.now)}</span>
              <span style={{ textAlign: "right" }}>
                {Math.round(z.predicted)} <span style={{ color: "var(--muted)" }}>{z.predictedDirection}</span>
              </span>
              <span style={{ textAlign: "right", fontWeight: 600 }}>
                {Math.round(z.actual)} <span style={{ color: "var(--muted)", fontWeight: 400 }}>{z.actualDirection}</span>
              </span>
              <span style={{ gridColumn: "1 / -1", color: z.verdict === "right" ? "var(--ok)" : "var(--warn)", fontWeight: 600, fontSize: 11 }}>
                {z.verdictText}
              </span>
            </div>
          ))}
        </div>
      )}
      {(score?.cams?.length ?? 0) > 0 && (
        <details style={{ fontSize: 12 }}>
          <summary style={{ cursor: "pointer", color: "var(--muted)" }}>{score?.cams?.length} cameras</summary>
          <div style={{ display: "grid", gap: 3, marginTop: 6 }}>
            {score?.cams?.map((c) => (
              <div key={c.camId} style={{ display: "flex", gap: 8 }}>
                <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{camName(c.camId)}</span>
                <span style={label}>pred {Math.round(c.predictedPeople)}</span>
                <span style={{ fontWeight: 600 }}>actual {Math.round(c.actualPeople)}</span>
              </div>
            ))}
          </div>
        </details>
      )}
      {score?.comparedAt && <div style={label}>Scored {new Date(score.comparedAt).toLocaleTimeString("en-GB")}</div>}
    </div>
  );
}
