"use client";

import dynamic from "next/dynamic";

// The player id and token live in localStorage: render in the browser only.
export const PortfolioClient = dynamic(() => import("./Portfolio").then((m) => m.Portfolio), { ssr: false });
