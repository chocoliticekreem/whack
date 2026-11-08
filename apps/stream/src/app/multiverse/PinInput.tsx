"use client";

import { useRef } from "react";

/** Four one-digit boxes; the phone shows its number pad. Typing moves on, backspace moves back, a paste fills all. */
export function PinInput({ value, onChange, label = "PIN" }: { value: string; onChange: (pin: string) => void; label?: string }) {
  const boxes = useRef<(HTMLInputElement | null)[]>([]);
  const set = (i: number, digit: string) => {
    const next = (value.slice(0, i) + digit + value.slice(i + 1)).slice(0, 4);
    onChange(next.replace(/\D/g, ""));
  };
  return (
    <div className="tl-pinbox" role="group" aria-label={label}>
      {[0, 1, 2, 3].map((i) => (
        <input
          key={i}
          ref={(el) => {
            boxes.current[i] = el;
          }}
          className="tl-data"
          inputMode="numeric"
          pattern="[0-9]*"
          autoComplete="off"
          maxLength={1}
          aria-label={`${label} digit ${i + 1}`}
          value={value[i] ?? ""}
          onFocus={(e) => e.currentTarget.select()}
          onChange={(e) => {
            const digits = e.target.value.replace(/\D/g, "");
            if (!digits) return;
            if (digits.length > 1) {
              onChange(digits.slice(0, 4));
              boxes.current[Math.min(3, digits.length)]?.focus();
              return;
            }
            set(Math.min(i, value.length), digits);
            boxes.current[Math.min(3, Math.min(i, value.length) + 1)]?.focus();
          }}
          onKeyDown={(e) => {
            if (e.key !== "Backspace") return;
            e.preventDefault();
            const at = value[i] ? i : Math.max(0, i - 1);
            onChange(value.slice(0, at));
            boxes.current[at]?.focus();
          }}
          onPaste={(e) => {
            e.preventDefault();
            const digits = e.clipboardData.getData("text").replace(/\D/g, "").slice(0, 4);
            onChange(digits);
            boxes.current[Math.min(3, digits.length)]?.focus();
          }}
        />
      ))}
    </div>
  );
}
