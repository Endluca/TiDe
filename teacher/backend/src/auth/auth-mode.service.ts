import {
  ForbiddenException,
  Injectable,
  ServiceUnavailableException,
} from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import type { AppEnvironment } from '../platform/config/environment';

export interface AuthCapabilities {
  authMode: 'HYBRID' | 'CRM_SSO_ONLY';
  crmSsoEnabled: boolean;
  passwordLoginEnabled: boolean;
  registrationEnabled: boolean;
  passwordResetEnabled: boolean;
  crmEntryUrl: string | null;
}

@Injectable()
export class AuthModeService {
  constructor(private readonly config: ConfigService<AppEnvironment, true>) {}

  capabilities(): AuthCapabilities {
    const authMode = this.config.get('TEACHER_AUTH_MODE', { infer: true });
    const localEnabled = authMode === 'HYBRID';

    return {
      authMode,
      crmSsoEnabled: Boolean(
        this.config.get('CRM_SSO_JWT_SECRET_CURRENT', { infer: true }),
      ),
      passwordLoginEnabled: localEnabled,
      registrationEnabled: localEnabled,
      passwordResetEnabled: localEnabled,
      crmEntryUrl: this.config.get('CRM_ENTRY_URL', { infer: true }) ?? null,
    };
  }

  assertLocalAuthEnabled(): void {
    if (this.capabilities().authMode === 'HYBRID') return;

    throw new ForbiddenException({
      code: 'LOCAL_AUTH_DISABLED',
      message: '请从 CRM 进入新师训练营',
      retryable: false,
    });
  }

  assertCrmSsoEnabled(): void {
    if (this.capabilities().crmSsoEnabled) return;

    throw new ServiceUnavailableException({
      code: 'CRM_SSO_NOT_CONFIGURED',
      message: 'CRM 登录暂不可用',
      retryable: true,
    });
  }
}
