import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "OverWatch",
  description: "Bet points on what London does in the next few minutes, against six AI traders. Real TfL data settles every bet.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className="h-full antialiased">
      <body className="h-full overflow-hidden">{children}</body>
    </html>
  );
}
