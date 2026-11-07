import { Syne } from "next/font/google";
import localFont from "next/font/local";

export const syne = Syne({ subsets: ["latin"], weight: ["700", "800"], variable: "--font-syne" });

export const departure = localFont({
  src: "../../../public/fonts/departure-mono/DepartureMono-Regular.woff2",
  variable: "--font-departure",
  display: "swap",
});
