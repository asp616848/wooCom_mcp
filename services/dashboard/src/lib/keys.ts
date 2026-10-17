/** Per-key tool-call rate limit (SO-7, D95): what the Keys table shows and what the edit form accepts. */

export const MAX_LIMIT = 100_000;

export function limitLabel(own: number | null, fallback: number): string {
  if (fallback === 0) return 'Off';
  return own === null ? `${fallback}/min (default)` : `${own}/min`;
}

/** Empty = back to the default (null); otherwise a whole number 1…100000, like the gateway. */
export function parseLimit(text: string): { value: number | null } | { error: string } {
  const trimmed = text.trim();
  if (trimmed === '') return { value: null };
  if (!/^\d+$/.test(trimmed)) return { error: 'Enter a whole number' };
  const value = Number(trimmed);
  if (value < 1 || value > MAX_LIMIT) return { error: `Between 1 and ${MAX_LIMIT.toLocaleString('en-US')}` };
  return { value };
}
