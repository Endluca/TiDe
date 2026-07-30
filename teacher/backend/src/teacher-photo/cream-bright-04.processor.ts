import { Injectable } from '@nestjs/common';
import sharp from 'sharp';

sharp.concurrency(2);

export const CREAM_BRIGHT_04_PRESET = 'cream_bright_04_adaptive_v3';

export interface CreamBright04Metrics {
  width: number;
  height: number;
  lumaMean: number;
  lumaMedian: number;
  lumaP98: number;
  faceMeterLuma: number;
  faceMeterLabB: number;
  brightScene: boolean;
  yellowScene: boolean;
  brightWhiteStrength: number;
  brightWhiteTargetLuma: number;
}

export interface CreamBright04Result {
  content: Buffer;
  mimeType: 'image/jpeg';
  strength: 0.75 | 1;
  metrics: CreamBright04Metrics;
}

interface DecodedImage {
  data: Buffer;
  width: number;
  height: number;
  channels: number;
}

const CURVE_X = [0, 32, 96, 160, 224, 255];
const CURVE_Y = [7, 42, 110, 172, 229, 252];
const TARGET_SKIN_LUMA = 166;
const BRIGHT_WHITE_TARGET_LUMA = 178;
const TEMPERATURE_RGB = [1.028, 1, 0.985] as const;

function clamp(value: number, low = 0, high = 255): number {
  return Math.min(high, Math.max(low, value));
}

function percentile(
  histogram: Uint32Array,
  count: number,
  quantile: number,
): number {
  const target = Math.max(0, Math.ceil(count * quantile) - 1);
  let seen = 0;
  for (let value = 0; value < histogram.length; value += 1) {
    seen += histogram[value];
    if (seen > target) return value;
  }
  return 255;
}

function median(values: number[]): number {
  if (values.length === 0) return 0;
  values.sort((left, right) => left - right);
  const middle = Math.floor(values.length / 2);
  return values.length % 2 === 0
    ? (values[middle - 1] + values[middle]) / 2
    : values[middle];
}

function curve(value: number): number {
  for (let index = 1; index < CURVE_X.length; index += 1) {
    if (value <= CURVE_X[index]) {
      const range = CURVE_X[index] - CURVE_X[index - 1];
      const position = (value - CURVE_X[index - 1]) / range;
      return (
        CURVE_Y[index - 1] + position * (CURVE_Y[index] - CURVE_Y[index - 1])
      );
    }
  }
  return CURVE_Y.at(-1)!;
}

function srgbToLinear(value: number): number {
  const normalized = value / 255;
  return normalized <= 0.04045
    ? normalized / 12.92
    : Math.pow((normalized + 0.055) / 1.055, 2.4);
}

function linearToSrgb(value: number): number {
  const normalized = clamp(value, 0, 1);
  return (
    255 *
    (normalized <= 0.0031308
      ? 12.92 * normalized
      : 1.055 * Math.pow(normalized, 1 / 2.4) - 0.055)
  );
}

function rgbToLab(r: number, g: number, b: number): [number, number, number] {
  const red = srgbToLinear(r);
  const green = srgbToLinear(g);
  const blue = srgbToLinear(b);
  const x = (0.4124564 * red + 0.3575761 * green + 0.1804375 * blue) / 0.95047;
  const y = 0.2126729 * red + 0.7151522 * green + 0.072175 * blue;
  const z = (0.0193339 * red + 0.119192 * green + 0.9503041 * blue) / 1.08883;
  const pivot = (value: number) =>
    value > 0.008856 ? Math.cbrt(value) : 7.787 * value + 16 / 116;
  const fx = pivot(x);
  const fy = pivot(y);
  const fz = pivot(z);
  return [116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)];
}

function labToRgb(
  light: number,
  a: number,
  b: number,
): [number, number, number] {
  const fy = (light + 16) / 116;
  const fx = fy + a / 500;
  const fz = fy - b / 200;
  const inversePivot = (value: number) => {
    const cube = value * value * value;
    return cube > 0.008856 ? cube : (value - 16 / 116) / 7.787;
  };
  const x = 0.95047 * inversePivot(fx);
  const y = inversePivot(fy);
  const z = 1.08883 * inversePivot(fz);
  const red = 3.2404542 * x - 1.5371385 * y - 0.4985314 * z;
  const green = -0.969266 * x + 1.8760108 * y + 0.041556 * z;
  const blue = 0.0556434 * x - 0.2040259 * y + 1.0572252 * z;
  return [linearToSrgb(red), linearToSrgb(green), linearToSrgb(blue)];
}

@Injectable()
export class CreamBright04Processor {
  async process(input: Buffer): Promise<CreamBright04Result> {
    const decoded = await this.decode(input);
    const sourceMetrics = this.measure(decoded);
    const brightWhiteStrength = this.brightWhiteStrength(sourceMetrics);
    const metrics: CreamBright04Metrics = {
      ...sourceMetrics,
      brightWhiteStrength: Number(brightWhiteStrength.toFixed(3)),
      brightWhiteTargetLuma: BRIGHT_WHITE_TARGET_LUMA,
    };
    const strength: 0.75 | 1 =
      metrics.brightScene || metrics.yellowScene ? 0.75 : 1;
    const fixed = this.renderFixed(decoded, metrics.faceMeterLuma);
    const blended = Buffer.alloc(decoded.data.length);

    for (
      let offset = 0;
      offset < decoded.data.length;
      offset += decoded.channels
    ) {
      for (let channel = 0; channel < 3; channel += 1) {
        const source = decoded.data[offset + channel];
        blended[offset + channel] = Math.round(
          source + strength * (fixed[offset + channel] - source),
        );
      }
      if (decoded.channels === 4)
        blended[offset + 3] = decoded.data[offset + 3];
    }
    const brightened = this.applyAdaptiveBrightWhite(
      blended,
      decoded.channels,
      brightWhiteStrength,
    );

    const content = await sharp(brightened, {
      raw: {
        width: decoded.width,
        height: decoded.height,
        channels: decoded.channels as 3 | 4,
      },
    })
      .jpeg({ quality: 92, chromaSubsampling: '4:4:4', mozjpeg: true })
      .toBuffer();

    return { content, mimeType: 'image/jpeg', strength, metrics };
  }

  private async decode(input: Buffer): Promise<DecodedImage> {
    const { data, info } = await sharp(input, { failOn: 'warning' })
      .rotate()
      .resize({
        width: 1920,
        height: 1080,
        fit: 'inside',
        withoutEnlargement: true,
      })
      .removeAlpha()
      .raw()
      .toBuffer({ resolveWithObject: true });
    if (info.width < 320 || info.height < 180 || info.channels < 3) {
      throw new Error('TEACHER_PHOTO_DIMENSIONS_INVALID');
    }
    return {
      data,
      width: info.width,
      height: info.height,
      channels: info.channels,
    };
  }

  private measure(image: DecodedImage): CreamBright04Metrics {
    const histogram = new Uint32Array(256);
    const seed: Array<{ luma: number; cb: number; cr: number; labB: number }> =
      [];
    let lumaSum = 0;
    const pixelCount = image.width * image.height;

    for (let y = 0; y < image.height; y += 1) {
      const ny = y / image.height;
      for (let x = 0; x < image.width; x += 1) {
        const offset = (y * image.width + x) * image.channels;
        const r = image.data[offset];
        const g = image.data[offset + 1];
        const b = image.data[offset + 2];
        const luma = Math.round(0.2126 * r + 0.7152 * g + 0.0722 * b);
        histogram[luma] += 1;
        lumaSum += luma;
        const nx = x / image.width;
        const faceX = (nx - 0.5) / 0.16;
        const faceY = (ny - 0.4) / 0.3;
        const inFace = faceX * faceX + faceY * faceY <= 1;
        const inCheekOrForehead =
          ((faceX + 0.42) / 0.32) ** 2 + ((faceY - 0.25) / 0.28) ** 2 <= 1 ||
          ((faceX - 0.42) / 0.32) ** 2 + ((faceY - 0.25) / 0.28) ** 2 <= 1 ||
          (faceX / 0.45) ** 2 + ((faceY + 0.35) / 0.22) ** 2 <= 1;
        const chroma = Math.max(r, g, b) - Math.min(r, g, b);
        if (
          inFace &&
          inCheekOrForehead &&
          luma > 8 &&
          luma < 250 &&
          chroma >= 6
        ) {
          const cb = 128 - 0.168736 * r - 0.331264 * g + 0.5 * b;
          const cr = 128 + 0.5 * r - 0.418688 * g - 0.081312 * b;
          if (cr - cb >= 6) {
            seed.push({ luma, cb, cr, labB: rgbToLab(r, g, b)[2] });
          }
        }
      }
    }

    const medianCb = median(seed.map((item) => item.cb));
    const medianCr = median(seed.map((item) => item.cr));
    const accepted = seed.filter(
      (item) =>
        Math.sqrt(
          ((item.cb - medianCb) / 20) ** 2 + ((item.cr - medianCr) / 24) ** 2,
        ) <= 1.55,
    );
    const globalMedian = percentile(histogram, pixelCount, 0.5);
    const faceMeterLuma =
      accepted.length >= 200
        ? median(accepted.map((item) => item.luma))
        : globalMedian;
    const faceMeterLabB =
      accepted.length >= 200
        ? accepted.reduce((sum, item) => sum + item.labB, 0) / accepted.length
        : 0;
    const lumaMean = lumaSum / pixelCount;
    const lumaP98 = percentile(histogram, pixelCount, 0.98);
    const brightScene =
      lumaP98 >= 245 ||
      lumaMean >= 195 ||
      globalMedian >= 215 ||
      faceMeterLuma >= 174;
    const yellowScene = faceMeterLabB >= 22;

    return {
      width: image.width,
      height: image.height,
      lumaMean: Number(lumaMean.toFixed(3)),
      lumaMedian: globalMedian,
      lumaP98,
      faceMeterLuma: Number(faceMeterLuma.toFixed(3)),
      faceMeterLabB: Number(faceMeterLabB.toFixed(3)),
      brightScene,
      yellowScene,
      brightWhiteStrength: 0,
      brightWhiteTargetLuma: BRIGHT_WHITE_TARGET_LUMA,
    };
  }

  private brightWhiteStrength(metrics: CreamBright04Metrics): number {
    const lumaNeed = clamp(
      (BRIGHT_WHITE_TARGET_LUMA - metrics.faceMeterLuma) / 45,
      0,
      1,
    );
    let strength = 0.3 + 0.7 * lumaNeed;
    if (metrics.brightScene && metrics.faceMeterLuma >= 165) {
      strength = Math.min(strength, 0.4);
    }
    if (metrics.yellowScene) {
      strength = Math.max(strength, 0.65);
    }
    return clamp(strength, 0.3, 1);
  }

  private applyAdaptiveBrightWhite(
    image: Buffer,
    channels: number,
    strength: number,
  ): Buffer {
    const output = Buffer.alloc(image.length);
    for (let offset = 0; offset < image.length; offset += channels) {
      const r = image[offset];
      const g = image[offset + 1];
      const b = image[offset + 2];
      const luma = 0.2126 * r + 0.7152 * g + 0.0722 * b;
      const lumaGuard = this.smoothstep(245, 185, luma);
      const channelGuard = this.smoothstep(250, 215, Math.max(r, g, b));
      const weight = strength * Math.min(lumaGuard, channelGuard);
      const targets = [
        this.highlightShoulder(1.06 * r + 6, r),
        this.highlightShoulder(1.06 * g + 6, g),
        this.highlightShoulder(1.09 * b + 8, b),
      ];
      output[offset] = Math.round(r + weight * (targets[0] - r));
      output[offset + 1] = Math.round(g + weight * (targets[1] - g));
      output[offset + 2] = Math.round(b + weight * (targets[2] - b));
      if (channels === 4) output[offset + 3] = image[offset + 3];
    }
    return output;
  }

  private highlightShoulder(value: number, source: number): number {
    if (value <= 228) return value;
    const compressed = 228 + 26 * (1 - Math.exp(-(value - 228) / 26));
    return Math.max(source, Math.min(254, compressed));
  }

  private smoothstep(edge0: number, edge1: number, value: number): number {
    const position = clamp((value - edge0) / (edge1 - edge0), 0, 1);
    return position * position * (3 - 2 * position);
  }

  private renderFixed(image: DecodedImage, faceMeterLuma: number): Buffer {
    const sourceMeter = clamp(faceMeterLuma, 1, 254);
    const gamma = clamp(
      Math.log(TARGET_SKIN_LUMA / 255) / Math.log(sourceMeter / 255),
      0.895,
      1.015,
    );
    const tone = Array.from({ length: 256 }, (_, value) =>
      curve(255 * Math.pow(value / 255, gamma)),
    );
    const whiteBalance = this.whiteBalance(image);
    const gains = whiteBalance.map(
      (gain, index) => gain * TEMPERATURE_RGB[index],
    );
    const output = Buffer.alloc(image.data.length);

    for (let offset = 0; offset < image.data.length; offset += image.channels) {
      let r = clamp(tone[image.data[offset]] * gains[0]);
      let g = clamp(tone[image.data[offset + 1]] * gains[1]);
      let b = clamp(tone[image.data[offset + 2]] * gains[2]);
      const lab = rgbToLab(r, g, b);
      [r, g, b] = labToRgb(lab[0], lab[1] + 0.4, lab[2] + 1.3);
      const luma = 0.2126 * r + 0.7152 * g + 0.0722 * b;
      const chroma = (Math.max(r, g, b) - Math.min(r, g, b)) / 255;
      const saturation = 0.975 + 0.03 * (1 - chroma);
      r = clamp(luma + saturation * (r - luma));
      g = clamp(luma + saturation * (g - luma));
      b = clamp(luma + saturation * (b - luma));
      output[offset] = Math.round(r);
      output[offset + 1] = Math.round(g);
      output[offset + 2] = Math.round(b);
      if (image.channels === 4) output[offset + 3] = image.data[offset + 3];
    }
    return output;
  }

  private whiteBalance(image: DecodedImage): [number, number, number] {
    const values: [number[], number[], number[]] = [[], [], []];
    for (let offset = 0; offset < image.data.length; offset += image.channels) {
      const r = image.data[offset];
      const g = image.data[offset + 1];
      const b = image.data[offset + 2];
      const luma = 0.2126 * r + 0.7152 * g + 0.0722 * b;
      if (
        Math.max(r, g, b) - Math.min(r, g, b) <= 14 &&
        luma >= 30 &&
        luma <= 245
      ) {
        values[0].push(r);
        values[1].push(g);
        values[2].push(b);
      }
    }
    if (values[0].length < 200) return [1, 1, 1];
    const medians = values.map((items) => median(items));
    const target = medians.reduce((sum, value) => sum + value, 0) / 3;
    const raw = medians.map((value) => target / Math.max(1, value));
    const gains = raw.map((value) =>
      clamp(1 + 0.65 * (value - 1), 0.948, 1.052),
    );
    const luminanceGain =
      0.2126 * gains[0] + 0.7152 * gains[1] + 0.0722 * gains[2];
    return gains.map((value) => value / luminanceGain) as [
      number,
      number,
      number,
    ];
  }
}
