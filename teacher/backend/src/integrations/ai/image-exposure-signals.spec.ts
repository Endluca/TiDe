import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import sharp from 'sharp';
import {
  analyzeCentralExposure,
  centralExposureFailure,
  prepareAiReviewImage,
} from './image-exposure-signals';

const fixture = (name: string) =>
  readFile(
    resolve(
      __dirname,
      '../../../../frontend/public/readiness/lesson-preparation-examples',
      name,
    ),
  );

describe('central exposure guard', () => {
  it("keeps Sophia's qualified frontal example inside the exposure limits", async () => {
    const signals = await analyzeCentralExposure(
      await fixture('camera-angle-good-front.jpg'),
    );

    expect(centralExposureFailure(signals)).toBeNull();
  });

  it.each([
    ['lighting-bad-glare.jpg', 'OVEREXPOSED'],
    ['lighting-bad-overexposed.jpg', 'OVEREXPOSED'],
    ['lighting-bad-dark.jpg', 'TOO_DARK'],
  ] as const)('rejects %s as %s', async (name, expected) => {
    const signals = await analyzeCentralExposure(await fixture(name));

    expect(centralExposureFailure(signals)).toBe(expected);
  });

  it('adds the camera guide only to the temporary AI copy', async () => {
    const original = await sharp({
      create: {
        width: 640,
        height: 360,
        channels: 3,
        background: { r: 32, g: 32, b: 32 },
      },
    })
      .jpeg()
      .toBuffer();

    const prepared = await prepareAiReviewImage(original, {
      includeCameraGuide: true,
    });
    const { data } = await sharp(prepared.content).raw().toBuffer({
      resolveWithObject: true,
    });
    let brightPixels = 0;
    for (let offset = 0; offset < data.length; offset += 3) {
      if (
        data[offset] > 150 &&
        data[offset + 1] > 150 &&
        data[offset + 2] > 150
      ) {
        brightPixels += 1;
      }
    }

    expect(brightPixels).toBeGreaterThan(100);
    expect(prepared.exposure.centralMeanLuma).toBeCloseTo(32, 0);
  });
});
