"use client";

import type { CSSProperties, ReactNode } from "react";

export const card: CSSProperties = {
  padding: "10px 12px",
  borderRadius: 12,
  background: "rgba(255,255,255,0.04)",
  border: "1px solid var(--border)",
  display: "grid",
  gap: 6,
};

export const label: CSSProperties = { color: "var(--muted)", fontSize: 11 };

export function Button({
  onClick,
  children,
  primary,
  active,
  disabled,
  small,
}: {
  onClick: () => void;
  children: ReactNode;
  primary?: boolean;
  active?: boolean;
  disabled?: boolean;
  small?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      style={{
        borderRadius: 999,
        padding: small ? "4px 10px" : "7px 14px",
        fontSize: small ? 11 : 12,
        fontWeight: 600,
        border: primary || active ? "1px solid transparent" : "1px solid var(--border-strong)",
        cursor: disabled ? "default" : "pointer",
        fontFamily: "inherit",
        background: primary ? "var(--ember)" : active ? "var(--text)" : "transparent",
        color: primary ? "#fff" : active ? "var(--bg)" : "var(--text)",
        opacity: disabled ? 0.5 : 1,
        transition: "background 120ms, color 120ms",
        whiteSpace: "nowrap",
      }}
    >
      {children}
    </button>
  );
}

export function Chip({ color, children }: { color: string; children: ReactNode }) {
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 6,
        padding: "2px 8px",
        borderRadius: 999,
        fontSize: 11,
        fontWeight: 600,
        border: `1px solid ${color}55`,
        color,
        background: `${color}14`,
        whiteSpace: "nowrap",
      }}
    >
      <span style={{ width: 6, height: 6, borderRadius: 999, background: color }} />
      {children}
    </span>
  );
}

export function Delta({ value, unit = "" }: { value: number; unit?: string }) {
  const color = value > 0 ? "#ff6a3d" : value < 0 ? "#4f9fe0" : "var(--muted)";
  return (
    <span style={{ color, fontWeight: 700, fontVariantNumeric: "tabular-nums" }}>
      {value > 0 ? "+" : ""}
      {Math.round(value)}
      {unit}
    </span>
  );
}

export function Row({ children, gap = 8 }: { children: ReactNode; gap?: number }) {
  return <div style={{ display: "flex", alignItems: "center", gap, flexWrap: "wrap" }}>{children}</div>;
}

/** Confidence as a word plus percent; the tooltip says what drives it. */
export function Confidence({ value }: { value: number }) {
  const word = value >= 0.65 ? "high" : value >= 0.4 ? "medium" : "low";
  const color = value >= 0.65 ? "var(--ok)" : value >= 0.4 ? "var(--warn)" : "var(--muted)";
  return (
    <span
      title="Share of this zone's streets with a live camera reading, plus a bonus when a camera sits in the zone; large swings lose a little."
      style={{ color, fontSize: 11, fontWeight: 600, cursor: "help", borderBottom: "1px dotted currentColor" }}
    >
      {word} confidence · {Math.round(value * 100)}%
    </span>
  );
}
