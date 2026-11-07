import type { Metadata } from "next";
import { MultiverseClient } from "./multiverse-client";

export const metadata: Metadata = {
  title: "Exchange of Worlds",
  description: "Parallel Londons, repriced by reality.",
};

export default function MultiversePage() {
  return <MultiverseClient />;
}
