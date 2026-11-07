import type { Metadata, Viewport } from "next";
import { Geist } from "next/font/google";
import { CityClient } from "./city-client";
import "./city.css";

const geist = Geist({ subsets: ["latin"], variable: "--font-geist" });

export const metadata: Metadata = {
  title: "Trade London",
  description: "Bet points on what London does in the next few minutes, against six AI traders. Real TfL data settles every bet.",
};

export const viewport: Viewport = { width: "device-width", initialScale: 1, viewportFit: "cover" };

export default function TradeLondonPage() {
  return (
    <div className={`tl-root ${geist.variable}`}>
      <CityClient />
    </div>
  );
}
