import sharp from 'sharp';
import {
  CREAM_BRIGHT_04_PRESET,
  CreamBright04Processor,
} from './cream-bright-04.processor';

async function solidPhoto(rgb: { r: number; g: number; b: number }) {
  return sharp({
    create: { width: 640, height: 360, channels: 3, background: rgb },
  })
    .jpeg()
    .toBuffer();
}

describe(CREAM_BRIGHT_04_PRESET, () => {
  const processor = new CreamBright04Processor();

  it('uses the protected 75% strength for an already bright scene', async () => {
    const result = await processor.process(
      await solidPhoto({ r: 235, g: 232, b: 226 }),
    );
    const metadata = await sharp(result.content).metadata();

    expect(result.strength).toBe(0.75);
    expect(result.metrics.brightScene).toBe(true);
    expect(result.metrics.brightWhiteStrength).toBe(0.3);
    expect(metadata.format).toBe('jpeg');
    expect(metadata.width).toBe(640);
    expect(metadata.height).toBe(360);
    const pixels = await sharp(result.content).raw().toBuffer();
    expect(pixels.includes(255)).toBe(false);
  });

  it('uses the full 100% strength for a neutral normal scene', async () => {
    const result = await processor.process(
      await solidPhoto({ r: 132, g: 132, b: 132 }),
    );

    expect(result.strength).toBe(1);
    expect(result.metrics.brightWhiteStrength).toBe(1);
    expect(result.metrics.brightScene).toBe(false);
    expect(result.metrics.yellowScene).toBe(false);
    const { data, info } = await sharp(result.content)
      .raw()
      .toBuffer({ resolveWithObject: true });
    const mean =
      data.reduce((sum, value) => sum + value, 0) /
      (info.width * info.height * info.channels);
    expect(mean).toBeGreaterThan(132);
  });
});
