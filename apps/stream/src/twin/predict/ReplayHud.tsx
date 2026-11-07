"use client";

import { skipHotspot, stopReplay, useReplay } from "./replay";
import { shownRun, usePredict } from "./store";
import { Button, Confidence } from "./ui";

const CSS = `
.replay-hud { position: absolute; inset: 0; z-index: 50; pointer-events: none; color: var(--text);
  font-family: var(--font-figtree), system-ui, sans-serif; }
.replay-clock { position: absolute; top: 64px; left: 50%; transform: translateX(-50%); text-align: center;
  padding: 10px 22px; border-radius: 16px; background: rgba(10,10,14,0.72); border: 1px solid var(--border-strong);
  backdrop-filter: blur(10px); -webkit-backdrop-filter: blur(10px); }
.replay-clock .min { font-size: 44px; font-weight: 800; line-height: 1; font-variant-numeric: tabular-nums; color: #ff6a3d; letter-spacing: -0.02em; }
.replay-clock .at { font-size: 13px; color: var(--muted); margin-top: 4px; font-variant-numeric: tabular-nums; }
.replay-caption { position: absolute; left: 50%; bottom: 196px; transform: translateX(-50%); max-width: min(760px, calc(100% - 32px));
  padding: 14px 22px; border-radius: 16px; background: rgba(10,10,14,0.8); border: 1px solid rgba(255,106,61,0.55);
  font-size: 28px; font-weight: 800; line-height: 1.2; text-align: center; backdrop-filter: blur(10px); -webkit-backdrop-filter: blur(10px);
  animation: replay-in 240ms ease-out; }
.replay-caption .sub { display: block; font-size: 14px; font-weight: 500; color: var(--muted); margin-top: 6px; }
@keyframes replay-in { from { opacity: 0; transform: translate(-50%, 10px); } to { opacity: 1; transform: translate(-50%, 0); } }
.replay-controls { position: absolute; left: 50%; bottom: 136px; transform: translateX(-50%); display: flex; gap: 8px; align-items: center;
  padding: 8px 10px; border-radius: 999px; background: rgba(10,10,14,0.72); border: 1px solid var(--border-strong); pointer-events: auto; }
.replay-bar { height: 3px; margin-top: 8px; border-radius: 2px; background: rgba(255,255,255,0.1); overflow: hidden; }
.replay-bar > span { display: block; height: 100%; background: #ff6a3d; }
.replay-tag { position: absolute; top: 14px; left: 50%; transform: translateX(-50%); font-size: 17px; font-weight: 800; letter-spacing: 0.06em;
  text-transform: uppercase; color: #ff6a3d; text-shadow: 0 2px 12px rgba(0,0,0,0.7); white-space: nowrap; }
@media (max-width: 640px) { .replay-clock .min { font-size: 30px; } .replay-caption { font-size: 15px; bottom: 220px; } .replay-controls { bottom: 160px; } }
`;

export function ReplayHud() {
  const active = useReplay((s) => s.active);
  const paused = useReplay((s) => s.paused);
  const speed = useReplay((s) => s.speed);
  const dwellIndex = useReplay((s) => s.dwellIndex);
  const hotspots = useReplay((s) => s.hotspots);
  const minute = usePredict((s) => s.minute);
  const run = usePredict(shownRun);
  if (!active || !run) return null;
  const m = Math.floor(minute);
  const at = new Date(run.at + minute * 60_000).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
  const hot = dwellIndex >= 0 ? hotspots[dwellIndex] : null;
  return (
    <>
      <style>{CSS}</style>
      <div className="replay-hud">
        <div className="replay-tag">Replay · {run.seed.area ?? "Tower Hill"} · next 30 min</div>
        <div className="replay-clock">
          <div className="min">+{m} min</div>
          <div className="at">{at}</div>
          <div className="replay-bar">
            <span style={{ width: `${(minute / 30) * 100}%` }} />
          </div>
        </div>
        {hot && (
          <div className="replay-caption" key={dwellIndex}>
            {hot.caption}
            <span className="sub">
              {Math.round(hot.item.now)} → {Math.round(hot.item.predicted)} people · <Confidence value={hot.item.confidence} />
              {hot.item.intervention ? " · what-if" : ""}
            </span>
          </div>
        )}
        <div className="replay-controls">
          <Button small onClick={() => useReplay.setState({ paused: !paused })}>
            {paused ? "Resume" : "Pause"}
          </Button>
          <Button small onClick={skipHotspot}>
            Skip
          </Button>
          {([1, 2, 4, 8] as const).map((s) => (
            <Button key={s} small active={speed === s} onClick={() => useReplay.setState({ speed: s })}>
              {s}×
            </Button>
          ))}
          <Button small onClick={stopReplay}>
            Exit
          </Button>
        </div>
      </div>
    </>
  );
}
