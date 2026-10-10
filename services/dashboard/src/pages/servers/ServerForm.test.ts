import { describe, expect, it } from 'vitest';

import { ApiError } from '@/lib/api';
import { describeError } from '@/pages/servers/ServerForm';

describe('describeError', () => {
  it('adds the allowed-URL hint to SSRF blocks', () => {
    const error = new ApiError(422, 'SSRF_BLOCKED', "'10.0.0.5' resolves to a non-public address (10.0.0.5)");
    const { code, message } = describeError(error);
    expect(code).toBe('SSRF_BLOCKED');
    expect(message).toContain('non-public address');
    expect(message).toContain('public https URLs');
  });

  it('passes other API errors through', () => {
    expect(describeError(new ApiError(409, 'CONFLICT', 'Slug taken'))).toEqual({
      code: 'CONFLICT',
      message: 'Slug taken',
    });
    expect(describeError(new Error('x')).code).toBe('ERROR');
  });
});
