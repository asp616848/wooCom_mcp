import { describe, expect, it } from 'vitest';

import { storeNameProblem } from '@/lib/stores';

describe('storeNameProblem', () => {
  it('accepts valid names and the empty field', () => {
    for (const name of ['', 'paris', 'tokyo2', 'abc', 'a234567890123456'])
      expect(storeNameProblem(name)).toBeNull();
  });

  it('explains invalid and reserved names', () => {
    for (const name of ['ab', 'Paris', '1paris', 'par-is', 'a2345678901234567']) {
      expect(storeNameProblem(name)).toMatch(/3–16 characters/);
    }
    expect(storeNameProblem('main')).toBe('"main" is reserved.');
  });
});
