import type { Metadata, Viewport } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "../city.css";
import "./exchange.css";
import { ExchangeClient } from "./exchange-client";

const geist = Geist({ subsets: ["latin"], variable: "--font-geist" });
const geistMono = Geist_Mono({ subsets: ["latin"], variable: "--font-geist-mono" });

export const metadata: Metadata = {
  title: "OverWatch · Exchange",
  description: "Trade London's next few minutes on a live order book against AI market makers, hedge funds and retail.",
};

export const viewport: Viewport = { width: "device-width", initialScale: 1, viewportFit: "cover" };

export default function ExchangePage() {
  return (
    <div className={`tl-root ${geist.variable} ${geistMono.variable}`}>
      <ExchangeClient />
    </div>
  );
}
