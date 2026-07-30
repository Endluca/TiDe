const REFRESH_TOKEN_KEY = "tide-refresh-token";
const REFRESH_EXPIRES_KEY = "tide-refresh-expires-at";

let accessToken = null;
let accessTokenExpiresAt = 0;

export function getAccessToken() {
  if (!accessToken || Date.now() >= accessTokenExpiresAt) return null;
  return accessToken;
}

export function getRefreshToken() {
  return window.sessionStorage.getItem(REFRESH_TOKEN_KEY);
}

export function saveTokenPair(tokenPair) {
  accessToken = tokenPair.accessToken;
  accessTokenExpiresAt = Date.now() + Math.max(0, tokenPair.accessTokenExpiresIn - 15) * 1000;
  window.sessionStorage.setItem(REFRESH_TOKEN_KEY, tokenPair.refreshToken);
  window.sessionStorage.setItem(REFRESH_EXPIRES_KEY, tokenPair.refreshTokenExpiresAt);
}

export function hasRestorableSession() {
  const refreshToken = getRefreshToken();
  const expiresAt = window.sessionStorage.getItem(REFRESH_EXPIRES_KEY);
  return Boolean(refreshToken && expiresAt && Date.parse(expiresAt) > Date.now());
}

export function clearSession() {
  accessToken = null;
  accessTokenExpiresAt = 0;
  window.sessionStorage.removeItem(REFRESH_TOKEN_KEY);
  window.sessionStorage.removeItem(REFRESH_EXPIRES_KEY);
}
