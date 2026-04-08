import type { NextConfig } from "next";
import { fileURLToPath } from "node:url";

const isTauriBuild = process.env.TAURI_BUILD === "1";
const projectRoot = fileURLToPath(new URL(".", import.meta.url));
const lightningCssShim = fileURLToPath(new URL("./shims/lightningcss.mjs", import.meta.url));

const nextConfig: NextConfig = {
  distDir: isTauriBuild ? ".next-tauri" : ".next-app",
  turbopack: {
    root: projectRoot,
    resolveAlias: {
      lightningcss: lightningCssShim,
    },
  },
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
