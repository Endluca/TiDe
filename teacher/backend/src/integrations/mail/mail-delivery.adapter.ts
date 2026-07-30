export interface VerificationEmailMessage {
  email: string;
  verificationUrl: string;
  expiresInMinutes: number;
  templateId: string;
}

export interface PasswordResetEmailMessage {
  email: string;
  resetUrl: string;
  expiresInMinutes: number;
  templateId: string;
}

export abstract class MailDeliveryAdapter {
  abstract sendVerificationEmail(
    message: VerificationEmailMessage,
  ): Promise<string | null>;

  abstract sendPasswordResetEmail(
    message: PasswordResetEmailMessage,
  ): Promise<string | null>;
}
