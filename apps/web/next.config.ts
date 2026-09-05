import type { NextConfig } from "next";

const config: NextConfig = {
  // Large validated import previews may exceed the default 30-second rewrite proxy timeout.
  // Use an explicit two-minute timeout for the same-origin API proxy.
  experimental: { proxyTimeout: 120_000 },
  async rewrites() {
    const backend = process.env.API_INTERNAL_URL ?? "http://127.0.0.1:8100";
    return [{ source: "/api/:path*", destination: `${backend}/api/:path*` }];
  },
  async headers() {
    return [{ source: "/:path*", headers: [
      { key: "X-Content-Type-Options", value: "nosniff" },
      { key: "Referrer-Policy", value: "same-origin" },
      { key: "X-Frame-Options", value: "DENY" }
    ] }];
  }
};
export default config;
