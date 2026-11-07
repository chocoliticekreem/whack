"use client";

import { useCallback, useEffect, useState, type RefObject } from "react";

/** Fullscreens the element behind `ref`, so any overlay children stay visible. */
export function useFullscreen<T extends HTMLElement>(ref: RefObject<T | null>) {
  const [active, setActive] = useState(false);

  useEffect(() => {
    const sync = () => setActive(document.fullscreenElement !== null && document.fullscreenElement === ref.current);
    document.addEventListener("fullscreenchange", sync);
    return () => document.removeEventListener("fullscreenchange", sync);
  }, [ref]);

  const toggle = useCallback(() => {
    if (document.fullscreenElement) void document.exitFullscreen().catch(() => {});
    else void ref.current?.requestFullscreen().catch(() => {});
  }, [ref]);

  return { active, toggle };
}
