"use client";

import dynamic from "next/dynamic";

// The account id and token live in localStorage: render in the browser only.
export const ExchangeClient = dynamic(() => import("./Exchange").then((m) => m.Exchange), { ssr: false });
