/** OAuth consent helpers (SO-3, D98): where the browser may be sent, and where sign-in returns to. */

const LOOPBACK = new Set(['localhost', '127.0.0.1', '[::1]']);

/** An in-app path to return to after sign-in; anything that could leave this site is dropped. */
export function safeNext(value: string | null): string | null {
  if (!value || !value.startsWith('/') || value.startsWith('//') || value.includes('\\')) return null;
  return value;
}

/**
 * The URL the browser is sent to after Approve/Deny: the app's own redirect URI (https, or http to this machine),
 * exactly the rule the gateway enforced when the app registered. Checked again here so a bad value can never
 * navigate the browser anywhere else.
 */
export function safeAppRedirect(value: string): string | null {
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    return null;
  }
  if (url.hash) return null;
  if (url.protocol === 'https:' && url.hostname) return url.toString();
  if (url.protocol === 'http:' && LOOPBACK.has(url.hostname)) return url.toString();
  return null;
}

/** `?req=<id>` of the consent page, only if it looks like the ids the gateway issues. */
export function consentRequestId(search: string): string | null {
  const id = new URLSearchParams(search).get('req');
  return id && /^[A-Za-z0-9_-]{16,128}$/.test(id) ? id : null;
}
