import { describe, expect, it } from 'vitest';

import { percent, rateTone } from '@/pages/InsightsPage';

describe('insights formatting', () => {
  it('formats rates as percentages', () => {
    expect(percent(0)).toBe('0.0%');
    expect(percent(0.25)).toBe('25.0%');
    expect(percent(0.004)).toBe('0.40%');
  });

  it('colours error rates by severity', () => {
    expect(rateTone(0.05)).toBe('green');
    expect(rateTone(0.06)).toBe('amber');
    expect(rateTone(0.21)).toBe('red');
  });
});
