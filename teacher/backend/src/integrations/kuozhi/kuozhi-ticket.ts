import { createHmac } from 'node:crypto';

function phpUrlEncode(value: string): string {
  return encodeURIComponent(value)
    .replace(
      /[!'()*~]/gu,
      (character) => `%${character.charCodeAt(0).toString(16).toUpperCase()}`,
    )
    .replace(/%20/gu, '+');
}

export function buildPhpHttpQuery(parameters: Record<string, string>): string {
  return Object.entries(parameters)
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([key, value]) => `${phpUrlEncode(key)}=${phpUrlEncode(value)}`)
    .join('&');
}

export interface KuozhiTicketInput {
  loginUrl: string;
  appKey: string;
  secretKey: string;
  teacherId: string;
  targetUrl: string;
}

export function createKuozhiTicketUrl(input: KuozhiTicketInput): string {
  const parameters = {
    appkey: input.appKey,
    type: 'teacher',
    id: input.teacherId,
    to: input.targetUrl,
  };
  const canonicalQuery = buildPhpHttpQuery(parameters);
  const hmacHex = createHmac('sha256', input.secretKey)
    .update(canonicalQuery)
    .digest('hex');
  const alt = Buffer.from(hmacHex, 'utf8').toString('base64');
  const url = new URL(input.loginUrl);
  for (const [key, value] of Object.entries(parameters)) {
    url.searchParams.set(key, value);
  }
  url.searchParams.set('alt', alt);
  return url.toString();
}
