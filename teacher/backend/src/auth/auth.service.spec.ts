import type { ConfigService } from '@nestjs/config';
import { UnprocessableEntityException } from '@nestjs/common';
import type { MailDeliveryAdapter } from '../integrations/mail/mail-delivery.adapter';
import type { ShiwenTeacherReadAdapter } from '../integrations/shiwen/shiwen-read.adapters';
import type { AppEnvironment } from '../platform/config/environment';
import type { AuthRepository } from './auth.repository';
import { AuthService } from './auth.service';
import type { AuthTokenService } from './auth-token.service';
import type { PasswordHasher } from './password-hasher';

interface AuthServiceFixture {
  service: AuthService;
  createRegistration: jest.Mock;
  markEmailDelivery: jest.Mock;
  sendVerificationEmail: jest.MockedFunction<
    MailDeliveryAdapter['sendVerificationEmail']
  >;
  passwordHash: jest.Mock;
  findIdentity: jest.Mock;
  consumeVerificationToken: jest.Mock;
  findPendingAccountByEmail: jest.Mock;
  createVerificationChallenge: jest.Mock;
}

function createFixture(): AuthServiceFixture {
  const values: Partial<AppEnvironment> = {
    NODE_ENV: 'test',
    PUBLIC_APP_URL: 'http://localhost:5173',
    ALLOWED_EMAIL_DOMAINS: '51talk.com',
    EMAIL_VERIFICATION_TTL_MINUTES: 60,
    MAIL_VERIFY_TEMPLATE_ID: 'verify-email',
  };
  const config = {
    get: jest.fn((key: keyof AppEnvironment) => values[key]),
  } as unknown as ConfigService<AppEnvironment, true>;
  const createRegistration = jest.fn().mockResolvedValue({
    accountId: 'account-id',
    accountStatus: 'PENDING_VERIFICATION',
  });
  const markEmailDelivery = jest.fn().mockResolvedValue(undefined);
  const consumeVerificationToken = jest.fn().mockResolvedValue('VERIFIED');
  const findPendingAccountByEmail = jest.fn().mockResolvedValue(null);
  const createVerificationChallenge = jest.fn().mockResolvedValue(undefined);
  const repository = {
    createRegistration,
    markEmailDelivery,
    consumeVerificationToken,
    findPendingAccountByEmail,
    createVerificationChallenge,
  } as unknown as AuthRepository;
  const findIdentity = jest.fn().mockResolvedValue({
    teacherId: 'TEACHER-001',
    campEnrollmentId: 'CAMP-001',
    name: 'Teacher',
    timezone: 'Asia/Shanghai',
    campDay: 1,
    graduationState: null,
    dataMode: 'REAL',
    sourceUpdatedAt: '2026-07-21T08:00:00.000Z',
  });
  const teacherReader = { findIdentity } as unknown as ShiwenTeacherReadAdapter;
  const sendVerificationEmail = jest.fn() as jest.MockedFunction<
    MailDeliveryAdapter['sendVerificationEmail']
  >;
  sendVerificationEmail.mockResolvedValue('provider-message-id');
  const mail = { sendVerificationEmail } as unknown as MailDeliveryAdapter;
  const passwordHash = jest.fn().mockResolvedValue('password-hash');
  const passwordHasher = { hash: passwordHash } as unknown as PasswordHasher;
  const tokenService = {
    create: jest.fn().mockReturnValue({
      raw: 'raw-verification-token',
      hash: 'stored-token-hash',
    }),
    hash: jest.fn().mockReturnValue('stored-token-hash'),
    hashPrivateValue: jest.fn().mockReturnValue('private-email-hash'),
  } as unknown as AuthTokenService;

  return {
    service: new AuthService(
      config,
      repository,
      teacherReader,
      mail,
      passwordHasher,
      tokenService,
    ),
    createRegistration,
    markEmailDelivery,
    sendVerificationEmail,
    passwordHash,
    findIdentity,
    consumeVerificationToken,
    findPendingAccountByEmail,
    createVerificationChallenge,
  };
}

describe('AuthService', () => {
  it('creates a pending binding and only sends the raw token to mail', async () => {
    const fixture = createFixture();

    await expect(
      fixture.service.register({
        email: ' Teacher@51Talk.com ',
        teacherId: 'TEACHER-001',
        password: 'a-secure-password',
      }),
    ).resolves.toEqual({
      status: 'VERIFICATION_REQUIRED',
      verificationEmailSent: true,
    });

    expect(fixture.findIdentity).toHaveBeenCalledWith('TEACHER-001');
    expect(fixture.passwordHash).toHaveBeenCalledWith('a-secure-password');
    expect(fixture.createRegistration).toHaveBeenCalledWith(
      expect.objectContaining({
        email: 'Teacher@51Talk.com',
        normalizedEmail: 'teacher@51talk.com',
        passwordHash: 'password-hash',
        tokenHash: 'stored-token-hash',
        recipientEmailHash: 'private-email-hash',
      }),
    );
    expect(JSON.stringify(fixture.createRegistration.mock.calls)).not.toContain(
      'raw-verification-token',
    );
    const sentMessage = fixture.sendVerificationEmail.mock.calls[0][0];
    expect(sentMessage.email).toBe('Teacher@51Talk.com');
    expect(sentMessage.verificationUrl).toContain('raw-verification-token');
    expect(fixture.markEmailDelivery).toHaveBeenCalledWith(
      expect.any(String),
      'SENT',
      'provider-message-id',
      null,
    );
  });

  it('rejects a Mock teacher identity before creating an account', async () => {
    const fixture = createFixture();
    fixture.findIdentity.mockResolvedValue({ dataMode: 'MOCK' });

    await expect(
      fixture.service.register({
        email: 'teacher@51talk.com',
        teacherId: 'MOCK-TEACHER',
        password: 'a-secure-password',
      }),
    ).rejects.toBeInstanceOf(UnprocessableEntityException);
    expect(fixture.createRegistration).not.toHaveBeenCalled();
  });

  it('does not reveal whether a resend email has an account', async () => {
    const fixture = createFixture();

    await expect(
      fixture.service.resendVerification('unknown@51talk.com'),
    ).resolves.toEqual({ accepted: true });
    expect(fixture.findPendingAccountByEmail).toHaveBeenCalledWith(
      'unknown@51talk.com',
    );
    expect(fixture.createVerificationChallenge).not.toHaveBeenCalled();
    expect(fixture.sendVerificationEmail).not.toHaveBeenCalled();
  });

  it('rejects an expired verification token', async () => {
    const fixture = createFixture();
    fixture.consumeVerificationToken.mockResolvedValue('INVALID_OR_EXPIRED');

    try {
      await fixture.service.confirmEmail('expired-token');
      throw new Error('expected confirmEmail to reject');
    } catch (error) {
      expect(error).toBeInstanceOf(UnprocessableEntityException);
      const response = (error as UnprocessableEntityException).getResponse();
      expect(response).toMatchObject({ code: 'VERIFICATION_TOKEN_INVALID' });
    }
  });
});
