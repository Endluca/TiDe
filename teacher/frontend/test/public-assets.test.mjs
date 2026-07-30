import assert from "node:assert/strict";
import test from "node:test";
import {
  defaultVideoAssetBaseUrl,
  publicAsset,
} from "../src/public-assets.js";

test("routes training videos through the playable OSS base by default", () => {
  assert.equal(
    publicAsset("/videos/g06-ttp-orientation/v1/01.mp4"),
    `${defaultVideoAssetBaseUrl}/videos/g06-ttp-orientation/v1/01.mp4`,
  );
});

test("keeps repository-native images on the current site", () => {
  assert.equal(
    publicAsset("/assets/brand/51talk-logo-blue.png"),
    "/assets/brand/51talk-logo-blue.png",
  );
});
