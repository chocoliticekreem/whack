"use client";

import dynamic from "next/dynamic";

// Browser-only app (webcam, BroadcastChannel, live clocks): skip prerendering entirely.
export const ClientShell = dynamic(
  () => import("./stream-shell").then((m) => m.StreamShell),
  { ssr: false },
);
