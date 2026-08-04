import { cp, mkdir, readFile, readdir, rm, writeFile } from "node:fs/promises";
import { join } from "node:path";
import {
  REQUIRED_SITES_PROJECT_ID,
  resolveSitesBuildConfig,
} from "./sites-build-config.mjs";

const root = process.cwd();
const dist = join(root, "dist");
const indexHtml = await readFile(join(dist, "index.html"), "utf8");
const hosting = await readFile(join(root, ".openai", "hosting.json"), "utf8");
const config = resolveSitesBuildConfig();
const hostingConfig = JSON.parse(hosting);

if (hostingConfig.project_id !== REQUIRED_SITES_PROJECT_ID) {
  throw new Error(
    `Sites project binding must remain ${REQUIRED_SITES_PROJECT_ID}`,
  );
}

const compiledAssetEntries = await readdir(join(dist, "assets"), {
  withFileTypes: true,
});
const compiledJavaScriptFiles = compiledAssetEntries.filter(
  (entry) => entry.isFile() && entry.name.endsWith(".js"),
);
const compiledJavaScript = await Promise.all(
  compiledJavaScriptFiles.map((entry) =>
    readFile(join(dist, "assets", entry.name), "utf8"),
  ),
);

for (const [name, value] of [
  ["VITE_API_BASE_URL", config.apiBaseUrl],
  ["VITE_PUBLIC_ASSET_BASE_URL", config.publicAssetBaseUrl],
]) {
  if (
    compiledJavaScript.length === 0 ||
    !compiledJavaScript.some((source) => source.includes(value))
  ) {
    throw new Error(`Sites build is missing ${name}: ${value}`);
  }
}

await mkdir(join(dist, "server"), { recursive: true });
await mkdir(join(dist, "client"), { recursive: true });
await mkdir(join(dist, ".openai"), { recursive: true });

const publicAssetEntries = await readdir(join(root, "public", "assets"), {
  withFileTypes: true,
});
for (const entry of publicAssetEntries) {
  await rm(join(dist, "assets", entry.name), {
    force: true,
    recursive: entry.isDirectory(),
  });
}

await cp(join(dist, "index.html"), join(dist, "client", "index.html"));
await cp(join(dist, "assets"), join(dist, "client", "assets"), { recursive: true });

const publicEntries = await readdir(join(root, "public"), { withFileTypes: true });
for (const entry of publicEntries) {
  if (entry.name.startsWith(".") || entry.name === "assets") continue;
  if (entry.name === "readiness") {
    await rm(join(dist, entry.name), { recursive: entry.isDirectory() });
    continue;
  }
  await cp(join(dist, entry.name), join(dist, "client", entry.name), {
    recursive: entry.isDirectory(),
  });
  await rm(join(dist, entry.name), { recursive: entry.isDirectory() });
}
await rm(join(dist, "assets"), { recursive: true });

await writeFile(join(dist, ".openai", "hosting.json"), hosting);

const worker = `
const INDEX_HTML = ${JSON.stringify(indexHtml)};

function htmlResponse(request) {
  const origin = new URL(request.url).origin;
  const html = INDEX_HTML.replaceAll("__SITE_ORIGIN__", origin);
  return new Response(request.method === "HEAD" ? null : html, {
    headers: {
      "content-type": "text/html; charset=utf-8",
      "cache-control": "no-cache",
    },
  });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const acceptsHtml = request.headers.get("accept")?.includes("text/html");

    if (
      (request.method === "GET" || request.method === "HEAD") &&
      (url.pathname === "/" || acceptsHtml)
    ) {
      return htmlResponse(request);
    }

    if (!env?.ASSETS?.fetch) {
      return new Response("Not found", { status: 404 });
    }

    let response = await env.ASSETS.fetch(request);
    if (response.status !== 404) return response;

    const clientUrl = new URL(request.url);
    clientUrl.pathname = \`/client\${url.pathname}\`;
    response = await env.ASSETS.fetch(new Request(clientUrl, request));
    return response;
  },
};
`;

await writeFile(join(dist, "server", "index.js"), worker);
