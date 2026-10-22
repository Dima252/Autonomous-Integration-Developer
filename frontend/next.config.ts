import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Plain static files in out/: S3 + CloudFront serve the public demo, Caddy the live
  // server (ADR 017). There's no Node server anywhere.
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
