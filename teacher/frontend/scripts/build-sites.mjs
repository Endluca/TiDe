import { buildFrontend } from "./build-vite.mjs";
import { resolvePublicAssetBaseUrl } from "./sites-build-config.mjs";

const publicAssetBaseUrl = resolvePublicAssetBaseUrl();

if (!/^https:\/\//i.test(publicAssetBaseUrl)) {
  throw new Error(
    "Sites builds require an HTTPS VITE_PUBLIC_ASSET_BASE_URL.",
  );
}

process.env.VITE_PUBLIC_ASSET_BASE_URL = publicAssetBaseUrl;

await buildFrontend();

await import("./prepare-sites-build.mjs");
