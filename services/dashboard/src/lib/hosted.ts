/** Hosted-server form rules, mirrored from the gateway (DESIGN §14, D94) so problems show before submitting. */
export const SLUG = /^[a-z][a-z0-9-]{1,29}$/;
export const IMAGE =
  /^[a-z0-9]+(?:[._-][a-z0-9]+)*(?::[0-9]+)?(?:\/[a-z0-9]+(?:[._-][a-z0-9]+)*)*:[A-Za-z0-9_][A-Za-z0-9._-]{0,127}$/;
export const IMAGE_PREFIXES = ['wmg/', 'ghcr.io/', 'docker.io/'];
const ENV_NAME = /^[A-Za-z_][A-Za-z0-9_]{0,63}$/;
export const MAX_ENV = 20;

export function slugProblem(slug: string): string | null {
  if (slug === '') return null;
  if (!SLUG.test(slug) || slug.includes('--') || slug.endsWith('-'))
    return '2–30 characters: lowercase letters, digits and single dashes, starting with a letter.';
  return null;
}

export function imageProblem(image: string): string | null {
  if (image === '') return null;
  if (!IMAGE.test(image)) return 'Use <repository>:<tag> with an explicit tag.';
  if (!IMAGE_PREFIXES.some((p) => image.startsWith(p)))
    return `Allowed registries: ${IMAGE_PREFIXES.join(', ')}`;
  return null;
}

/** "KEY=value" per line (blank lines and # comments ignored) → object, or the first problem found. */
export function parseEnv(text: string): { env: Record<string, string>; problem: string | null } {
  const env: Record<string, string> = {};
  const lines = text.split('\n').map((l) => l.trim());
  for (const line of lines) {
    if (line === '' || line.startsWith('#')) continue;
    const eq = line.indexOf('=');
    if (eq < 1) return { env, problem: `"${line.slice(0, 40)}" is not KEY=value` };
    const name = line.slice(0, eq).trim();
    if (!ENV_NAME.test(name)) return { env, problem: `"${name}" is not a valid variable name` };
    const upper = name.toUpperCase();
    if (upper === 'PORT' || upper.startsWith('MCP_'))
      return { env, problem: `${name} is set by the platform (PORT and MCP_* are reserved)` };
    if (name in env) return { env, problem: `${name} is set twice` };
    env[name] = line.slice(eq + 1);
  }
  if (Object.keys(env).length > MAX_ENV) return { env, problem: `At most ${MAX_ENV} variables` };
  return { env, problem: null };
}
