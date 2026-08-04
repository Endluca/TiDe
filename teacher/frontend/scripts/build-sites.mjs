import { buildFrontend } from "./build-vite.mjs";
import { resolveSitesBuildConfig } from "./sites-build-config.mjs";
import { loadEnv } from "vite";

const config = resolveSitesBuildConfig({
  ...loadEnv("production", process.cwd(), "VITE_"),
  ...process.env,
});

process.env.VITE_API_BASE_URL = config.apiBaseUrl;
process.env.VITE_PUBLIC_ASSET_BASE_URL = config.publicAssetBaseUrl;

await buildFrontend({ apiBaseUrl: config.apiBaseUrl });

await import("./prepare-sites-build.mjs");
