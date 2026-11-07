"use client";

import dynamic from "next/dynamic";

// WebGL canvas is browser-only: skip prerendering entirely.
export const GameClient = dynamic(() => import("./Game").then((m) => m.Game), { ssr: false });
