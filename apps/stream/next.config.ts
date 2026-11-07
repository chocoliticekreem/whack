import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  /* config options here */
  cacheComponents: true,
  // `npm run tunnel` exposes the dev server over HTTPS so a phone can use camera + GPS.
  // Private LAN ranges let a phone on the same Wi-Fi play /multiverse against `next dev -H 0.0.0.0`.
  allowedDevOrigins: ["*.trycloudflare.com", "10.*.*.*", "192.168.*.*", "172.*.*.*"],
  partialPrefetching: true,
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
