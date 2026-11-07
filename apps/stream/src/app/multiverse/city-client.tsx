"use client";

import dynamic from "next/dynamic";

// WebGL canvas is browser-only: skip prerendering entirely.
export const CityClient = dynamic(() => import("./City").then((m) => m.City), { ssr: false });
