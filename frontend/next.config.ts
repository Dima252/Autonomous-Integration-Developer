import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Plain static files in out/: Caddy serves them on the live server, and the e2e tests
  // and README media run against them (ADR 017, 018). There's no Node server anywhere.
  output: "export",
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
