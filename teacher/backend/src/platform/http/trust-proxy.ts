interface TrustProxyApplication {
  set(setting: string, value: unknown): unknown;
}

export function configureTrustProxy(
  app: TrustProxyApplication,
  trustedHopCount: number,
): void {
  app.set('trust proxy', trustedHopCount);
}
