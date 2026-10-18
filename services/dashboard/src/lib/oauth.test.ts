import { describe, expect, it } from 'vitest';

import { consentRequestId, safeAppRedirect, safeNext } from '@/lib/oauth';

describe('sign-in return path', () => {
  it('keeps in-app paths only', () => {
    expect(safeNext('/oauth/consent?req=abc')).toBe('/oauth/consent?req=abc');
    expect(safeNext('/servers')).toBe('/servers');
    for (const bad of [
      null,
      '',
      'servers',
      '//evil.com',
      'https://evil.com',
      '/\\evil.com',
      'javascript:alert(1)',
    ])
      expect(safeNext(bad)).toBeNull();
  });
});

describe('where an approved or denied request sends the browser', () => {
  it('allows https and loopback http', () => {
    expect(safeAppRedirect('https://app.example.com/cb?code=1&state=2')).toBe(
      'https://app.example.com/cb?code=1&state=2',
    );
    expect(safeAppRedirect('http://localhost:8765/callback?code=1')).toBe(
      'http://localhost:8765/callback?code=1',
    );
    expect(safeAppRedirect('http://127.0.0.1:3000/cb')).toBe('http://127.0.0.1:3000/cb');
    expect(safeAppRedirect('http://[::1]:3000/cb')).toBe('http://[::1]:3000/cb');
  });

  it('refuses everything else', () => {
    for (const bad of [
      'http://example.com/cb',
      'http://localhost.evil.com/cb',
      'javascript:alert(1)',
      'data:text/html,hi',
      'myapp://callback',
      'https://app.example.com/cb#x',
      'not a url',
      '',
    ])
      expect(safeAppRedirect(bad)).toBeNull();
  });
});

describe('consent request id', () => {
  it('reads a plausible id from the query string', () => {
    expect(consentRequestId('?req=AbCdEfGhIjKlMnOpQrStUv-_1234')).toBe('AbCdEfGhIjKlMnOpQrStUv-_1234');
    for (const bad of ['', '?req=', '?req=short', '?req=has spaces in it 123456', '?other=1'])
      expect(consentRequestId(bad)).toBeNull();
  });
});
