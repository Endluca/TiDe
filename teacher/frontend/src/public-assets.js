const configuredBaseUrl = (
  import.meta.env?.VITE_PUBLIC_ASSET_BASE_URL || ""
).replace(/\/$/, "");
export const defaultVideoAssetBaseUrl =
  "https://tide-media.51talkjr.com";

export function publicAsset(path) {
  if (!path || /^(?:https?:|data:|blob:)/i.test(path)) return path;
  const normalizedPath = path.startsWith("/") ? path : `/${path}`;
  const baseUrl = configuredBaseUrl
    || (normalizedPath.startsWith("/videos/") ? defaultVideoAssetBaseUrl : "");
  return baseUrl ? `${baseUrl}${normalizedPath}` : normalizedPath;
}

export const publicAssetBaseUrl = configuredBaseUrl;
