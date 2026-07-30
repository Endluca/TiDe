import { apiRequest } from "./api-client";
import { clearSession, saveTokenPair } from "./session-store";

export async function registerTeacher(input) {
  return apiRequest("/api/v1/auth/register", { method: "POST", body: input, auth: false });
}

export async function confirmEmail(token) {
  return apiRequest("/api/v1/auth/email-verification/confirm", {
    method: "POST",
    body: { token },
    auth: false,
  });
}

export async function resendVerification(email) {
  return apiRequest("/api/v1/auth/email-verification/resend", {
    method: "POST",
    body: { email },
    auth: false,
  });
}

export async function loginTeacher(input) {
  const tokens = await apiRequest("/api/v1/auth/login", {
    method: "POST",
    body: input,
    auth: false,
  });
  saveTokenPair(tokens);
  return tokens;
}

export async function logoutTeacher() {
  try {
    await apiRequest("/api/v1/auth/logout", { method: "POST" });
  } finally {
    clearSession();
  }
}

export async function requestPasswordReset(email) {
  return apiRequest("/api/v1/auth/password-reset/request", {
    method: "POST",
    body: { email },
    auth: false,
  });
}

export async function confirmPasswordReset(token, newPassword) {
  return apiRequest("/api/v1/auth/password-reset/confirm", {
    method: "POST",
    body: { token, newPassword },
    auth: false,
  });
}
