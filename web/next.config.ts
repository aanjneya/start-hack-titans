import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  /* config options here */
  cacheComponents: true,
  partialPrefetching: true,
  experimental: {
    turbopackPluginRuntimeStrategy: "forceWorkerThreads",
  },
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
  webpack: (config) => {
    for (const rule of config.module?.rules ?? []) {
      if (rule && typeof rule === "object" && "oneOf" in rule && Array.isArray(rule.oneOf)) {
        for (const sub of rule.oneOf) {
          if (sub && typeof sub === "object" && Array.isArray(sub.use)) {
            const hasCssLoader = sub.use.some(
              (u: unknown) =>
                typeof u === "object" &&
                u !== null &&
                "loader" in u &&
                typeof (u as { loader: unknown }).loader === "string" &&
                (u as { loader: string }).loader.includes("css-loader") &&
                !(u as { loader: string }).loader.includes("postcss-loader"),
            );
            if (hasCssLoader) {
              sub.use.push("@tailwindcss/turbopack");
            }
          }
        }
      }
    }
    return config;
  },
};

export default nextConfig;
