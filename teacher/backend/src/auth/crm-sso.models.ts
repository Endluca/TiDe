export interface CrmSsoClaims {
  iat: number;
  exp: number;
  jti: string;
  iss: string;
  aud: string;
  teacherId: string;
  email: string;
}

export type CrmSsoBindingFailure =
  | 'EMAIL_ALREADY_BOUND'
  | 'TEACHER_ALREADY_BOUND'
  | 'ACCOUNT_NOT_ACTIVE'
  | 'CRM_SSO_REPLAYED';

export class CrmSsoBindingError extends Error {
  constructor(public readonly reason: CrmSsoBindingFailure) {
    super(reason);
    this.name = 'CrmSsoBindingError';
  }
}
