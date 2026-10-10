import { afterEach, describe, expect, it, vi } from 'vitest';

import { api, ApiError } from './api';

function mockFetch(status: number, body: unknown) {
  const fn = vi.fn(async () => new Response(body === null ? null : JSON.stringify(body), { status }));
  vi.stubGlobal('fetch', fn);
  return fn;
}

afterEach(() => vi.unstubAllGlobals());

describe('api', () => {
  it('sends the CSRF header, cookies and JSON body', async () => {
    const fetch = mockFetch(200, { user: { id: '1' } });
    await api('/auth/login', 'POST', { email: 'rohan@example.com' });
    const [url, init] = fetch.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe('/api/auth/login');
    expect(init.credentials).toBe('include');
    expect(init.headers).toMatchObject({ 'X-WMG-CSRF': '1', 'Content-Type': 'application/json' });
    expect(init.body).toBe('{"email":"rohan@example.com"}');
  });

  it('turns the error envelope into ApiError', async () => {
    mockFetch(401, { error: { code: 'UNAUTHENTICATED', message: 'Please sign in' } });
    await expect(api('/auth/me')).rejects.toEqual(new ApiError(401, 'UNAUTHENTICATED', 'Please sign in'));
  });

  it('handles 204 and non-JSON errors', async () => {
    mockFetch(204, null);
    await expect(api('/auth/logout', 'POST')).resolves.toBeUndefined();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response('<html>bad gateway</html>', { status: 502 })),
    );
    await expect(api('/x')).rejects.toMatchObject({ status: 502, code: 'HTTP_ERROR' });
  });
});
