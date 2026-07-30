function requireHttpsUrl(name, value) {
  const normalized = String(value || "").trim().replace(/\/$/, "");
  if (!normalized) {
    throw new Error(`${name} is required for an Nginx production build.`);
  }

  let url;
  try {
    url = new URL(normalized);
  } catch {
    throw new Error(`${name} must be an absolute HTTPS URL.`);
  }
  if (url.protocol !== "https:") {
    throw new Error(`${name} must use HTTPS.`);
  }
  if (url.username || url.password) {
    throw new Error(`${name} must not contain credentials.`);
  }

  return { normalized, url };
}

export function resolveNginxBuildConfig(environment = process.env) {
  const api = requireHttpsUrl(
    "VITE_API_BASE_URL",
    environment.VITE_API_BASE_URL,
  );
  if (
    api.url.pathname !== "/" ||
    api.url.search.length > 0 ||
    api.url.hash.length > 0
  ) {
    throw new Error(
      "VITE_API_BASE_URL must be an origin without a path, query, or fragment.",
    );
  }

  const publicAssets = requireHttpsUrl(
    "VITE_PUBLIC_ASSET_BASE_URL",
    environment.VITE_PUBLIC_ASSET_BASE_URL,
  );

  return {
    apiBaseUrl: api.url.origin,
    publicAssetBaseUrl: publicAssets.normalized,
    siteOrigin: api.url.origin,
  };
}

export function replaceSiteOrigin(html, siteOrigin) {
  if (!html.includes("__SITE_ORIGIN__")) {
    throw new Error("Built index.html is missing the __SITE_ORIGIN__ marker.");
  }
  return html.replaceAll("__SITE_ORIGIN__", siteOrigin);
}
