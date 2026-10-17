import { describe, expect, it } from 'vitest';

import { limitLabel, parseLimit } from '@/lib/keys';

describe('key rate limits', () => {
  it('labels the effective limit', () => {
    expect(limitLabel(null, 120)).toBe('120/min (default)');
    expect(limitLabel(500, 120)).toBe('500/min');
    expect(limitLabel(500, 0)).toBe('Off');
  });

  it('parses the edit field like the gateway validates it', () => {
    expect(parseLimit('')).toEqual({ value: null });
    expect(parseLimit(' 250 ')).toEqual({ value: 250 });
    expect(parseLimit('100000')).toEqual({ value: 100000 });
    for (const bad of ['0', '100001', '-5', '2.5', 'abc']) expect(parseLimit(bad)).toHaveProperty('error');
  });
});
