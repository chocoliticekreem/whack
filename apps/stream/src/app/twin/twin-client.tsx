"use client";

import dynamic from "next/dynamic";

// WebGL canvas is browser-only: skip prerendering entirely.
export const TwinClient = dynamic(() => import("@/twin/World").then((m) => m.World), {
  ssr: false,
});
