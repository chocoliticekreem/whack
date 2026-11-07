import type { Metadata, Viewport } from "next";
import { Geist } from "next/font/google";
import { GameClient } from "../game-client";
import "../game.css";

const geist = Geist({ subsets: ["latin"], variable: "--font-geist" });

export const metadata: Metadata = {
  title: "Which London is real?",
  description: "Pick which future of Oxford Circus happens next, against the AI forecasters. Real London decides.",
};

export const viewport: Viewport = { width: "device-width", initialScale: 1, viewportFit: "cover" };

export default function OxfordGamePage() {
  return (
    <div className={`mvg-root ${geist.variable}`}>
      <GameClient />
    </div>
  );
}
