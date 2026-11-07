"use client";

import { useEffect, useState, type CSSProperties } from "react";

const FMT = new Intl.DateTimeFormat("en-GB", {
  timeZone: "Europe/London",
  weekday: "short",
  day: "numeric",
  month: "short",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
});

/** London wall-clock time, ticking every second. Shown on camera clips, which TfL publishes a few minutes apart. */
export function useLondonTime(withDate = true) {
  const [now, setNow] = useState<Date | null>(null);
  useEffect(() => {
    const tick = () => setNow(new Date());
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, []);
  if (!now) return "";
  const parts = Object.fromEntries(FMT.formatToParts(now).map((p) => [p.type, p.value]));
  const time = `${parts.hour}:${parts.minute}:${parts.second}`;
  return withDate ? `${parts.weekday} ${parts.day} ${parts.month} ${time}` : time;
}

/** Covers the camera's burned-in timestamp (top-left of a TfL frame) with the live time. */
export function LiveClock({ style }: { style?: CSSProperties }) {
  const text = useLondonTime();
  return (
    <span
      style={{
        position: "absolute",
        left: 0,
        top: 0,
        // Wide and tall enough to hide TfL's own "Sat 17 Oct 15:45" stamp.
        minWidth: "36%",
        minHeight: "7%",
        display: "flex",
        alignItems: "center",
        padding: "4px 10px",
        background: "#000",
        color: "#fff",
        fontFamily: "var(--font-mono, ui-monospace), monospace",
        fontWeight: 700,
        fontSize: 17,
        fontVariantNumeric: "tabular-nums",
        letterSpacing: 0.3,
        pointerEvents: "none",
        zIndex: 2,
        ...style,
      }}
    >
      {text}
    </span>
  );
}
