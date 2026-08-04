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
  const publicAssets = requireHttpsUrl(
    "VITE_PUBLIC_ASSET_BASE_URL",
    environment.VITE_PUBLIC_ASSET_BASE_URL,
  );

  return {
    apiBaseUrl: "",
    publicAssetBaseUrl: publicAssets.normalized,
  };
}
