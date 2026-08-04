import sharp from 'sharp';

export const CENTRAL_EXPOSURE_MIN_MEAN_LUMA = 60;
export const CENTRAL_EXPOSURE_MAX_CLIPPED_RATIO = 0.2;

export interface CentralExposureSignals {
  centralMeanLuma: number;
  centralClippedLumaRatio: number;
}

export interface PreparedAiReviewImage {
  content: Buffer;
  exposure: CentralExposureSignals;
}

export interface PrepareAiReviewImageOptions {
  includeCameraGuide?: boolean;
}

export async function prepareAiReviewImage(
  source: Buffer,
  options: PrepareAiReviewImageOptions = {},
): Promise<PreparedAiReviewImage> {
  const normalized = await sharp(source, { failOn: 'warning' })
    .rotate()
    .resize({
      width: 1280,
      height: 720,
      fit: 'inside',
      withoutEnlargement: true,
    })
    .jpeg({ quality: 85, mozjpeg: true })
    .toBuffer();
  const exposure = await analyzeCentralExposure(normalized);
  if (!options.includeCameraGuide) {
    return { content: normalized, exposure };
  }
  const metadata = await sharp(normalized).metadata();
  if (!metadata.width || !metadata.height) {
    throw new Error('IMAGE_REVIEW_DIMENSIONS_UNAVAILABLE');
  }
  const guide =
    Buffer.from(`<svg width="${metadata.width}" height="${metadata.height}" viewBox="0 0 640 360" xmlns="http://www.w3.org/2000/svg">
    <g fill="none" stroke="rgba(255,255,255,0.82)" stroke-width="2" stroke-dasharray="8 7">
      <ellipse cx="320" cy="105" rx="45" ry="58" />
      <path d="M190 310 C198 242 236 202 287 187 C296 184 302 177 304 166 M336 166 C338 177 344 184 353 187 C404 202 442 242 450 310" />
    </g>
  </svg>`);
  const content = await sharp(normalized)
    .composite([{ input: guide, blend: 'over' }])
    .jpeg({ quality: 85, mozjpeg: true })
    .toBuffer();
  return {
    content,
    exposure,
  };
}

export async function analyzeCentralExposure(
  source: Buffer,
): Promise<CentralExposureSignals> {
  const { data, info } = await sharp(source, { failOn: 'warning' })
    .removeAlpha()
    .raw()
    .toBuffer({ resolveWithObject: true });
  const left = Math.floor(info.width * 0.4);
  const right = Math.max(left + 1, Math.ceil(info.width * 0.6));
  const top = Math.floor(info.height * 0.2);
  const bottom = Math.max(top + 1, Math.ceil(info.height * 0.55));
  let pixelCount = 0;
  let lumaTotal = 0;
  let clippedCount = 0;

  for (let y = top; y < bottom; y += 1) {
    for (let x = left; x < right; x += 1) {
      const offset = (y * info.width + x) * info.channels;
      const luma =
        0.2126 * data[offset] +
        0.7152 * data[offset + 1] +
        0.0722 * data[offset + 2];
      pixelCount += 1;
      lumaTotal += luma;
      if (luma >= 245) clippedCount += 1;
    }
  }

  if (pixelCount === 0) {
    throw new Error('IMAGE_REVIEW_EXPOSURE_REGION_EMPTY');
  }
  return {
    centralMeanLuma: lumaTotal / pixelCount,
    centralClippedLumaRatio: clippedCount / pixelCount,
  };
}

export function centralExposureFailure(
  signals: CentralExposureSignals,
): 'TOO_DARK' | 'OVEREXPOSED' | null {
  if (signals.centralMeanLuma < CENTRAL_EXPOSURE_MIN_MEAN_LUMA) {
    return 'TOO_DARK';
  }
  if (signals.centralClippedLumaRatio > CENTRAL_EXPOSURE_MAX_CLIPPED_RATIO) {
    return 'OVEREXPOSED';
  }
  return null;
}
