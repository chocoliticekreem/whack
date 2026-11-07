import type { Metadata, Viewport } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "../city.css";
import "./portfolio.css";
import { PortfolioClient } from "./portfolio-client";

const geist = Geist({ subsets: ["latin"], variable: "--font-geist" });
const geistMono = Geist_Mono({ subsets: ["latin"], variable: "--font-geist-mono" });

export const metadata: Metadata = {
  title: "OverWatch · Portfolio",
  description: "Your bets, positions, P&L and ledger on OverWatch.",
};

export const viewport: Viewport = { width: "device-width", initialScale: 1, viewportFit: "cover" };

export default function PortfolioPage() {
  return (
    <div className={`tl-root ${geist.variable} ${geistMono.variable}`}>
      <PortfolioClient />
    </div>
  );
}
