import { describe, expect, it } from 'vitest';

import { imageProblem, parseEnv, slugProblem } from '@/lib/hosted';

describe('hosted server form rules', () => {
  it('checks slugs like the gateway', () => {
    expect(slugProblem('pincode-hosted')).toBeNull();
    expect(slugProblem('')).toBeNull();
    for (const bad of ['Pincode', '1abc', 'a--b', 'abc-', 'a']) expect(slugProblem(bad)).not.toBeNull();
  });

  it('needs an explicit tag from an allowed registry', () => {
    expect(imageProblem('wmg/mcp-pincode:abc123')).toBeNull();
    expect(imageProblem('ghcr.io/owner/wmg-mcp-pincode:c194f93')).toBeNull();
    expect(imageProblem('wmg/mcp-pincode')).toMatch(/explicit tag/);
    expect(imageProblem('quay.io/x/y:1')).toMatch(/Allowed registries/);
  });

  it('parses KEY=value lines and rejects reserved or repeated names', () => {
    expect(parseEnv('A=1\n\n# note\nB = x=y')).toEqual({ env: { A: '1', B: ' x=y' }, problem: null });
    expect(parseEnv('PORT=1').problem).toMatch(/reserved/);
    expect(parseEnv('mcp_auth_token=1').problem).toMatch(/reserved/);
    expect(parseEnv('A=1\nA=2').problem).toMatch(/twice/);
    expect(parseEnv('no-equals').problem).toMatch(/KEY=value/);
    expect(parseEnv('1X=1').problem).toMatch(/valid variable name/);
  });
});
