"use client";

import { CircleHelp } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

const SEEN_KEY = "overwatch.howItWorks.v1";

const STEPS = [
  "Pick a question about London's next 5 minutes, or click any spot on the map.",
  "Back an answer with points. The less likely it is, the more it pays.",
  "Real TfL data decides. Winners get paid.",
];

function seen(): boolean {
  try {
    return window.localStorage.getItem(SEEN_KEY) === "1";
  } catch {
    return true;
  }
}

function markSeen() {
  try {
    window.localStorage.setItem(SEEN_KEY, "1");
  } catch {
    // Blocked storage: the guide simply shows again next visit.
  }
}

/** The header's "How it works" link; it also opens the three-step guide by itself on a first visit. */
export function HowItWorks() {
  const btn = useRef<HTMLButtonElement>(null);
  const [host, setHost] = useState<HTMLElement | null>(null);

  useEffect(() => {
    if (!seen()) setHost(btn.current?.closest<HTMLElement>(".tl") ?? null);
  }, []);

  const close = () => {
    markSeen();
    setHost(null);
  };

  return (
    <>
      <button
        ref={btn}
        type="button"
        className="tl-how-btn"
        onClick={(e) => {
          e.stopPropagation();
          setHost(e.currentTarget.closest<HTMLElement>(".tl"));
        }}
      >
        <CircleHelp size={16} />
        <span className="tl-desk">How it works</span>
      </button>
      {host &&
        createPortal(
          <div
            className="tl-modal-bg"
            onClick={(e) => {
              e.stopPropagation();
              close();
            }}
          >
            <div className="tl-modal tl-how" role="dialog" aria-modal aria-label="How OverWatch works" onClick={(e) => e.stopPropagation()}>
              <h2>How OverWatch works</h2>
              <ol>
                {STEPS.map((s, i) => (
                  <li key={s}>
                    <span className="tl-how-n tl-data">{i + 1}</span>
                    <span>{s}</span>
                  </li>
                ))}
              </ol>
              <button type="button" className="tl-confirm" onClick={close}>
                Got it
              </button>
            </div>
          </div>,
          host,
        )}
    </>
  );
}
