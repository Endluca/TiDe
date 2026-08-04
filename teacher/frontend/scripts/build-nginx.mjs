import { readFile, readdir, rm } from "node:fs/promises";
import { join } from "node:path";
import { buildFrontend } from "./build-vite.mjs";
import { resolveNginxBuildConfig } from "./nginx-build-config.mjs";

const root = process.cwd();
const dist = join(root, "dist");
const config = resolveNginxBuildConfig();

process.env.VITE_PUBLIC_ASSET_BASE_URL = config.publicAssetBaseUrl;

await buildFrontend({ apiBaseUrl: config.apiBaseUrl });

const compiledAssetEntries = await readdir(join(dist, "assets"), {
  withFileTypes: true,
});
const compiledJavaScript = await Promise.all(
  compiledAssetEntries
    .filter((entry) => entry.isFile() && entry.name.endsWith(".js"))
    .map((entry) => readFile(join(dist, "assets", entry.name), "utf8")),
);
if (
  !compiledJavaScript.some((source) =>
    source.includes(config.publicAssetBaseUrl),
  )
) {
  throw new Error(
    `Nginx build is missing VITE_PUBLIC_ASSET_BASE_URL: ${config.publicAssetBaseUrl}`,
  );
}

const localAssetEntries = await readdir(join(root, "public", "assets"), {
  withFileTypes: true,
});
for (const entry of localAssetEntries) {
  await rm(join(dist, "assets", entry.name), {
    force: true,
    recursive: entry.isDirectory(),
  });
}
await rm(join(dist, "readiness"), { force: true, recursive: true });

const indexPath = join(dist, "index.html");
const indexHtml = await readFile(indexPath, "utf8");
const siteOriginMarkers = indexHtml.match(/__SITE_ORIGIN__/g) || [];
if (siteOriginMarkers.length !== 2) {
  throw new Error(
    `Nginx build must retain exactly two runtime site origin markers; found ${siteOriginMarkers.length}.`,
  );
}
