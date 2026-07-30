export const DEFAULT_PUBLIC_ASSET_BASE_URL =
  "https://tide-media.51talkjr.com";
export const REQUIRED_SITES_PROJECT_ID =
  "appgprj_6a5ef5ac7c8081918973cee7ac42b9c1";

export function resolvePublicAssetBaseUrl(environment = process.env) {
  return (
    environment.VITE_PUBLIC_ASSET_BASE_URL ||
    DEFAULT_PUBLIC_ASSET_BASE_URL
  ).replace(/\/$/, "");
}
