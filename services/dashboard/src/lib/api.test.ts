import { afterEach, describe, expect, it, vi } from 'vitest';

import { api, ApiError, auditApi, keysApi, serversApi } from './api';

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

describe('endpoint helpers', () => {
  it('builds audit query strings without empty filters', async () => {
    const fetch = mockFetch(200, { items: [], nextCursor: null });
    await auditApi.list({
      serverSlug: 'gst',
      outcome: 'timeout',
      userId: undefined,
      cursor: '42',
      limit: 50,
    });
    await auditApi.list({});
    const urls = fetch.mock.calls.map((call) => (call as unknown as [string])[0]);
    expect(urls).toEqual(['/api/audit?serverSlug=gst&outcome=timeout&cursor=42&limit=50', '/api/audit']);
  });

  it('lists keys for me, a user, or everyone', async () => {
    const fetch = mockFetch(200, { keys: [] });
    await keysApi.list();
    await keysApi.list({ userId: 'u 1' });
    await keysApi.list({ all: true });
    const urls = fetch.mock.calls.map((call) => (call as unknown as [string])[0]);
    expect(urls).toEqual(['/api/keys', '/api/keys?userId=u%201', '/api/keys?all=1']);
  });

  it('sends credential null to clear and omits it to keep', async () => {
    const fetch = mockFetch(200, { server: {} });
    await serversApi.update('s1', { credential: null });
    await serversApi.update('s1', { name: 'X' });
    const bodies = fetch.mock.calls.map((call) => (call as unknown as [string, RequestInit])[1].body);
    expect(bodies).toEqual(['{"credential":null}', '{"name":"X"}']);
  });
});
