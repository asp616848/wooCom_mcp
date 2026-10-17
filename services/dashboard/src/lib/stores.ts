/** Store name rules, mirrored from the gateway (DESIGN §13.1) so the form explains problems before submitting. */
export const STORE_NAME = /^[a-z][a-z0-9]{2,15}$/;
export const RESERVED_STORE_NAMES = ['main', 'admin', 'api', 'www', 'shop', 'dashboard', 'gateway', 'test'];

export function storeNameProblem(name: string): string | null {
  if (name === '') return null;
  if (!STORE_NAME.test(name)) return '3–16 characters: lowercase letters and digits, starting with a letter.';
  if (RESERVED_STORE_NAMES.includes(name)) return `"${name}" is reserved.`;
  return null;
}
