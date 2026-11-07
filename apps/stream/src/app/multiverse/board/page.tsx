import type { Metadata } from "next";
import { departure, syne } from "../fonts";
import { MultiverseClient } from "../multiverse-client";
import "../theme.css";

export const metadata: Metadata = {
  title: "Exchange of Worlds · Details",
  description: "Parallel Londons, repriced by reality.",
};

export default function MultiversePage() {
  return (
    <div className={`mv ${syne.variable} ${departure.variable}`}>
      <MultiverseClient />
    </div>
  );
}
