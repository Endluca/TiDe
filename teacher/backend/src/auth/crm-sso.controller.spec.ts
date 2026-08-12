import { HttpStatus, UnauthorizedException } from '@nestjs/common';
import type { Response } from 'express';
import { CrmSsoController } from './crm-sso.controller';
import type { CrmSsoService } from './crm-sso.service';

function createController() {
  const start = jest.fn();
  const failureRedirect = jest.fn((error: unknown) => {
    expect(error).toBeInstanceOf(UnauthorizedException);
    return 'https://tide.example.test/sso/callback?error=CRM_SSO_TOKEN_INVALID';
  });
  const controller = new CrmSsoController({
    start,
    failureRedirect,
  } as unknown as CrmSsoService);
  const setHeader = jest.fn();
  const redirect = jest.fn();
  const response = {
    setHeader,
    redirect,
  } as unknown as Response;

  return { controller, start, failureRedirect, response, setHeader, redirect };
}

describe('CrmSsoController', () => {
  it('redirects malformed entry requests to the safe frontend error page', async () => {
    const fixture = createController();

    await fixture.controller.enter({}, fixture.response);

    expect(fixture.start).not.toHaveBeenCalled();
    expect(fixture.failureRedirect).toHaveBeenCalledTimes(1);
    expect(fixture.redirect).toHaveBeenCalledWith(
      HttpStatus.FOUND,
      'https://tide.example.test/sso/callback?error=CRM_SSO_TOKEN_INVALID',
    );
    expect(fixture.setHeader).toHaveBeenCalledWith('Cache-Control', 'no-store');
  });
});
