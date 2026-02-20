import type { NextConfig } from "next";

const isTauriBuild = process.env.TAURI_BUILD === "1";

const nextConfig: NextConfig = {
  distDir: isTauriBuild ? ".next-tauri" : ".next-app",
  ...(isTauriBuild
    ? {
        output: "export",
        trailingSlash: true,
        images: {
          unoptimized: true,
        },
        // Tauri packaging runs in a constrained build context; skip type/lint gates here.
        typescript: {
          ignoreBuildErrors: true,
        },
      }
    : {}),
};

export default nextConfig;
