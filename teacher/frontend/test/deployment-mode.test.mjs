import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

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
});
