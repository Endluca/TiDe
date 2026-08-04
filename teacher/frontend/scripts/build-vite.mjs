import { build } from "vite";
import { gzipSync } from "node:zlib";
import { readdir, readFile, stat } from "node:fs/promises";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

export async function buildFrontend({ apiBaseUrl } = {}) {
  const config = {
    build: {
      rolldownOptions: {
        output: {
          codeSplitting: {
            minSize: 20_000,
            maxSize: 450_000,
            groups: [
              {
                name: "framework",
                test: /node_modules\/(react|react-dom|react-router|react-router-dom|scheduler)\//,
                priority: 30,
              },
              {
                name: "animation",
                test: /node_modules\/(@gsap|gsap)\//,
                priority: 20,
              },
              {
                name: "icons",
                test: /node_modules\/@phosphor-icons\//,
                priority: 10,
              },
            ],
          },
        },
      },
    },
  };
  if (apiBaseUrl !== undefined) {
    config.define = {
      "import.meta.env.VITE_API_BASE_URL": JSON.stringify(apiBaseUrl),
    };
  }
  await build(config);

  const assetsDirectory = new URL("../dist/assets/", import.meta.url);
  const assetsPath = fileURLToPath(assetsDirectory);
  const javascriptFiles = (await readdir(assetsDirectory)).filter((filename) =>
    filename.endsWith(".js"),
  );
  const oversizedFiles = [];
  for (const filename of javascriptFiles) {
    const path = join(assetsPath, filename);
    const bytes = (await stat(path)).size;
    if (bytes > 500_000) oversizedFiles.push(`${filename} (${bytes} bytes)`);
  }
  if (oversizedFiles.length > 0) {
    throw new Error(
      `JavaScript chunk budget exceeded: ${oversizedFiles.join(", ")}`,
    );
  }

  const html = await readFile(
    new URL("../dist/index.html", import.meta.url),
    "utf8",
  );
  const entryName = html.match(/src="[^"]*\/assets\/([^"]+\.js)"/)?.[1];
  if (!entryName) throw new Error("Could not find the frontend entry chunk.");
  const entry = await readFile(
    new URL(`../dist/assets/${entryName}`, import.meta.url),
  );
  const entryGzipBytes = gzipSync(entry).length;
  if (entryGzipBytes > 220_000) {
    throw new Error(
      `Initial JavaScript gzip budget exceeded: ${entryGzipBytes} bytes`,
    );
  }
}
