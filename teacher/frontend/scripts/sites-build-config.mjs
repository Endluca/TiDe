export const DEFAULT_PUBLIC_ASSET_BASE_URL =
  "https://tide-media.51talkjr.com";
export const REQUIRED_SITES_PROJECT_ID =
  "appgprj_6a5ef5ac7c8081918973cee7ac42b9c1";

function requireHttpsOrigin(name, value) {
  let url;
  try {
    ({ url } = requireHttpsUrl(name, value));
  } catch (error) {
    if (error instanceof Error && error.message.includes("is required")) {
      throw new Error(`${name} is required for a cross-origin Sites build.`);
    }
    throw new Error(`${name} must be an absolute HTTPS origin.`);
  }
  if (url.pathname !== "/") {
    throw new Error(`${name} must be an absolute HTTPS origin.`);
  }
  return url.origin;
}

function requireHttpsUrl(name, value) {
  const normalized = String(value || "").trim().replace(/\/+$/, "");
  if (!normalized) {
    throw new Error(`${name} is required for a Sites build.`);
  }

  let url;
  try {
    url = new URL(normalized);
  } catch {
    throw new Error(`${name} must be an absolute HTTPS URL.`);
  }
  if (
    url.protocol !== "https:" ||
    url.username ||
    url.password ||
    url.search ||
    url.hash
  ) {
    throw new Error(`${name} must be an absolute HTTPS URL.`);
  }
  return { normalized, url };
}

export function resolvePublicAssetBaseUrl(environment = process.env) {
  return requireHttpsUrl(
    "VITE_PUBLIC_ASSET_BASE_URL",
    environment.VITE_PUBLIC_ASSET_BASE_URL || DEFAULT_PUBLIC_ASSET_BASE_URL,
  ).normalized;
}

export function resolveSitesBuildConfig(environment = process.env) {
  return {
    apiBaseUrl: requireHttpsOrigin(
      "VITE_API_BASE_URL",
      environment.VITE_API_BASE_URL,
    ),
    publicAssetBaseUrl: resolvePublicAssetBaseUrl(environment),
  };
}
