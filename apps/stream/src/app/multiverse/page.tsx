import type { Metadata, Viewport } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import { CityClient } from "./city-client";
import "./city.css";

const geist = Geist({ subsets: ["latin"], variable: "--font-geist" });
const geistMono = Geist_Mono({ subsets: ["latin"], variable: "--font-geist-mono" });

export const metadata: Metadata = {
  title: "OverWatch",
  description: "Bet points on what London does in the next few minutes, against six AI traders. Real TfL data settles every bet.",
};

export const viewport: Viewport = { width: "device-width", initialScale: 1, viewportFit: "cover" };

export default function OverWatchPage() {
  return (
    <div className={`tl-root ${geist.variable} ${geistMono.variable}`}>
      <CityClient />
    </div>
  );
}
