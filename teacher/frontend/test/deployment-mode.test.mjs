import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import viteConfig from "../vite.config.js";

test("uses the reviewed Nginx build as the repository default", async () => {
  const packageJson = JSON.parse(
    await readFile(new URL("../package.json", import.meta.url), "utf8"),
  );
  const dockerfile = await readFile(
    new URL("../Dockerfile", import.meta.url),
    "utf8",
  );

  assert.equal(
    packageJson.scripts.build,
    "node scripts/build-nginx.mjs",
  );
  assert.equal(packageJson.scripts["build:nginx"], packageJson.scripts.build);
  assert.match(dockerfile, /RUN pnpm run build:nginx/);
  assert.doesNotMatch(dockerfile, /ARG VITE_API_BASE_URL/);
  assert.doesNotMatch(dockerfile, /ENV VITE_API_BASE_URL/);
  assert.match(dockerfile, /COPY vite\.config\.js \.\//);
});

test("proxies same-origin API paths during local development and preview", () => {
  const config = viteConfig({ command: "serve", mode: "development" });
  for (const mode of ["server", "preview"]) {
    assert.equal(
      config[mode].proxy["/api"].target,
      "http://127.0.0.1:3000",
    );
  }
  assert.equal(config.define["import.meta.env.VITE_API_BASE_URL"], '""');
});
