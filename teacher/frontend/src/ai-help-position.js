export const AI_HELP_MOBILE_BREAKPOINT = 720;
export const AI_HELP_DESKTOP_SIZE = 84;
export const AI_HELP_MOBILE_SIZE = 62;

const finiteOr = (value, fallback) =>
  Number.isFinite(value) ? value : fallback;

export function getAiHelpBounds({
  viewportWidth,
  viewportHeight,
  headerBottom = 0,
  lowerBoundary = viewportHeight,
  buttonSize,
  edgeGap,
}) {
  const width = Math.max(0, finiteOr(viewportWidth, 0));
  const height = Math.max(0, finiteOr(viewportHeight, 0));
  const size = Math.max(0, finiteOr(buttonSize, 0));
  const gap = Math.max(0, finiteOr(edgeGap, 0));
  const minX = Math.min(gap, Math.max(0, width - size));
  const minY = Math.min(
    Math.max(gap, finiteOr(headerBottom, 0) + gap),
    Math.max(0, height - size),
  );

  return {
    minX,
    maxX: Math.max(minX, width - size - gap),
    minY,
    maxY: Math.max(
      minY,
      Math.min(height, finiteOr(lowerBoundary, height)) - size - gap,
    ),
  };
}

export function clampAiHelpPosition(position, bounds) {
  const x = finiteOr(position?.x, bounds.maxX);
  const y = finiteOr(position?.y, bounds.maxY);
  return {
    x: Math.min(bounds.maxX, Math.max(bounds.minX, x)),
    y: Math.min(bounds.maxY, Math.max(bounds.minY, y)),
  };
}

export function defaultAiHelpPosition(bounds, lift = 0) {
  return {
    x: bounds.maxX,
    y: Math.max(bounds.minY, bounds.maxY - Math.max(0, finiteOr(lift, 0))),
  };
}
