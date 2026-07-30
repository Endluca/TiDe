import { readFile, readdir, rm, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { buildFrontend } from "./build-vite.mjs";
import {
  replaceSiteOrigin,
  resolveNginxBuildConfig,
} from "./nginx-build-config.mjs";

const root = process.cwd();
const dist = join(root, "dist");
const config = resolveNginxBuildConfig();

process.env.VITE_API_BASE_URL = config.apiBaseUrl;
process.env.VITE_PUBLIC_ASSET_BASE_URL = config.publicAssetBaseUrl;

await buildFrontend();

const compiledAssetEntries = await readdir(join(dist, "assets"), {
  withFileTypes: true,
});
const compiledJavaScript = await Promise.all(
  compiledAssetEntries
    .filter((entry) => entry.isFile() && entry.name.endsWith(".js"))
    .map((entry) => readFile(join(dist, "assets", entry.name), "utf8")),
);
for (const [name, value] of [
  ["VITE_API_BASE_URL", config.apiBaseUrl],
  ["VITE_PUBLIC_ASSET_BASE_URL", config.publicAssetBaseUrl],
]) {
  if (!compiledJavaScript.some((source) => source.includes(value))) {
    throw new Error(`Nginx build is missing ${name}: ${value}`);
  }
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
const renderedIndexHtml = replaceSiteOrigin(indexHtml, config.siteOrigin);
if (renderedIndexHtml.includes("__SITE_ORIGIN__")) {
  throw new Error("Nginx build still contains an unresolved site origin.");
}
await writeFile(indexPath, renderedIndexHtml);
