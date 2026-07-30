export interface RegistrationAccepted {
  status: 'VERIFICATION_REQUIRED';
  verificationEmailSent: boolean;
}

export interface EmailVerificationResult {
  status: 'VERIFIED' | 'ALREADY_VERIFIED';
}

export interface VerificationResendAccepted {
  accepted: true;
}

export interface RegistrationRecord {
  accountId: string;
  accountStatus: string;
}

export type ConsumeVerificationResult =
  'VERIFIED' | 'ALREADY_VERIFIED' | 'INVALID_OR_EXPIRED';

export interface AuthTokenPair {
  tokenType: 'Bearer';
  accessToken: string;
  accessTokenExpiresIn: number;
  refreshToken: string;
  refreshTokenExpiresAt: string;
}

export interface AuthPrincipal {
  accountId: string;
  sessionId: string;
}

export interface PasswordResetAccepted {
  accepted: true;
}

export interface PasswordResetCompleted {
  status: 'PASSWORD_UPDATED';
}
