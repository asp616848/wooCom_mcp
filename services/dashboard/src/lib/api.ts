/**
 * Fetch wrapper for the gateway's /api (DESIGN §11): same-origin cookie session, CSRF header on every call,
 * JSON errors `{error:{code,message}}` turned into ApiError.
 */

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
  }
}

type Method = 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';

export async function api<T>(path: string, method: Method = 'GET', body?: unknown): Promise<T> {
  const response = await fetch(`/api${path}`, {
    method,
    credentials: 'include',
    headers: {
      Accept: 'application/json',
      'X-WMG-CSRF': '1',
      ...(body === undefined ? {} : { 'Content-Type': 'application/json' }),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (response.status === 204) return undefined as T;
  const data: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const error = (data as { error?: { code?: string; message?: string } } | null)?.error;
    throw new ApiError(
      response.status,
      error?.code ?? 'HTTP_ERROR',
      error?.message ?? `Request failed (${response.status})`,
    );
  }
  return data as T;
}

export type Role = 'admin' | 'member';

export interface User {
  id: string;
  email: string;
  name: string;
  role: Role;
  status: 'active' | 'disabled';
  createdAt: string;
  lastLoginAt: string | null;
}

export const authApi = {
  me: () => api<{ user: User }>('/auth/me'),
  login: (email: string, password: string) => api<{ user: User }>('/auth/login', 'POST', { email, password }),
  logout: () => api<undefined>('/auth/logout', 'POST'),
};

// ---- servers (DESIGN §7.3) ----
export type ServerStatus = 'connected' | 'unreachable' | 'disabled' | 'unknown';
export type AuthType = 'none' | 'bearer' | 'header';

export interface McpServer {
  id: string;
  slug: string;
  name: string;
  status: ServerStatus;
  enabled: boolean;
  toolCount: number;
  source: string;
  protocolEra: string | null;
  lastCheckedAt: string | null;
  // admin only
  url?: string;
  authType?: AuthType;
  authHeaderName?: string | null;
  hasCredential?: boolean;
  timeoutMs?: number;
  lastOkAt?: string | null;
  lastErrorCode?: string | null;
  lastError?: string | null;
  createdAt?: string;
  updatedAt?: string;
}

export interface ServerTool {
  name: string;
  exposedName: string;
  title: string | null;
  description: string | null;
  inputSchema: Record<string, unknown>;
  adminOnly: boolean;
}

export interface ServerInput {
  slug?: string;
  name?: string;
  url?: string;
  authType?: AuthType;
  authHeaderName?: string | null;
  /** string = set, null = clear, omitted = keep */
  credential?: string | null;
  timeoutMs?: number;
  enabled?: boolean;
}

export interface TestResult {
  ok: boolean;
  era: string | null;
  toolCount: number | null;
  errorCode: string | null;
  error: string | null;
}

export const serversApi = {
  list: () => api<{ servers: McpServer[] }>('/servers'),
  tools: (id: string) => api<{ tools: ServerTool[] }>(`/servers/${id}/tools`),
  test: (body: ServerInput) => api<TestResult>('/servers/test', 'POST', body),
  create: (body: ServerInput) => api<{ server: McpServer }>('/servers', 'POST', body),
  update: (id: string, body: ServerInput) => api<{ server: McpServer }>(`/servers/${id}`, 'PATCH', body),
  remove: (id: string) => api<undefined>(`/servers/${id}`, 'DELETE'),
  /** SO-2: admin-only on/off for one tool (by its upstream name). */
  setPolicy: (id: string, tool: string, adminOnly: boolean) =>
    api<{ policy: ToolPolicy }>(`/servers/${id}/tools/${encodeURIComponent(tool)}/policy`, 'PUT', {
      adminOnly,
    }),
};

export interface ToolPolicy {
  serverId: string;
  toolName: string;
  adminOnly: boolean;
  updatedAt: string;
}

// ---- keys ----
export interface ApiKey {
  id: string;
  name: string;
  prefix: string;
  userId: string;
  userEmail: string;
  createdAt: string;
  lastUsedAt: string | null;
  revokedAt: string | null;
  /** the key's own tool-call limit per minute; null = the gateway default (D95) */
  rateLimitPerMin: number | null;
}

export interface KeyList {
  keys: ApiKey[];
  /** 0 = rate limiting off */
  defaultRateLimitPerMin: number;
}

export interface CreatedKey {
  id: string;
  name: string;
  prefix: string;
  /** plaintext, returned once */
  key: string;
}

export const keysApi = {
  list: (params: { userId?: string; all?: boolean } = {}) => {
    const query = params.all ? '?all=1' : params.userId ? `?userId=${encodeURIComponent(params.userId)}` : '';
    return api<KeyList>(`/keys${query}`);
  },
  setLimit: (id: string, rateLimitPerMin: number | null) =>
    api<{ key: ApiKey }>(`/keys/${id}`, 'PATCH', { rateLimitPerMin }),
  create: (name: string, userId?: string) => api<CreatedKey>('/keys', 'POST', { name, userId }),
  revoke: (id: string) => api<undefined>(`/keys/${id}`, 'DELETE'),
};

// ---- users & invites ----
export interface Invite {
  id: string;
  email: string;
  role: Role;
  expiresAt: string;
  createdAt: string;
}

export const usersApi = {
  list: () => api<{ users: User[] }>('/users'),
  update: (id: string, body: { role?: Role; status?: User['status'] }) =>
    api<{ user: User }>(`/users/${id}`, 'PATCH', body),
};

export const invitesApi = {
  list: () => api<{ invites: Invite[] }>('/invites'),
  create: (email: string, role: Role) =>
    api<{ inviteUrl: string; expiresAt: string }>('/invites', 'POST', { email, role }),
  revoke: (id: string) => api<undefined>(`/invites/${id}`, 'DELETE'),
  info: (token: string) =>
    api<{ email: string; role: Role; valid: boolean }>(`/invites/${encodeURIComponent(token)}`),
  accept: (token: string, name: string, password: string) =>
    api<{ user: User }>(`/invites/${encodeURIComponent(token)}/accept`, 'POST', { name, password }),
};

// ---- audit ----
export type Outcome =
  'success' | 'tool_error' | 'upstream_error' | 'timeout' | 'denied' | 'unknown_tool' | 'rate_limited';

export interface AuditItem {
  id: number;
  ts: string;
  userId: string | null;
  userEmail: string | null;
  credentialType: string | null;
  method: string;
  serverSlug: string | null;
  toolName: string | null;
  exposedName: string | null;
  outcome: Outcome;
  durationMs: number;
  errorMessage: string | null;
  argsSummary: unknown;
  clientName: string | null;
  clientMode: string;
  requestId: string | null;
}

export interface AuditFilters {
  userId?: string;
  serverSlug?: string;
  outcome?: Outcome;
  cursor?: string;
  limit?: number;
}

export const auditApi = {
  list: (filters: AuditFilters) => {
    const params = new URLSearchParams();
    for (const [key, value] of Object.entries(filters)) {
      if (value !== undefined && value !== '') params.set(key, String(value));
    }
    const query = params.toString();
    return api<{ items: AuditItem[]; nextCursor: string | null }>(`/audit${query ? `?${query}` : ''}`);
  },
};

// ---- connect ----
export interface ConnectInfo {
  mcpUrl: string;
  searchMcpUrl: string | null;
  snippets: { claudeCode: string; cursor: string; inspector: string };
}

export const KEY_PLACEHOLDER = '<YOUR_API_KEY>';

export const connectApi = {
  info: () => api<ConnectInfo>('/connect-info'),
};

// ---- stores (bonus, DESIGN §13.1) ----
export type StoreStatus = 'provisioning' | 'ready' | 'failed' | 'deleting';

export interface Store {
  name: string;
  status: StoreStatus;
  statusReason: string | null;
  url: string;
  attempt: number;
  owner: string;
  toolsPrefix: string;
  createdAt: string;
  readyAt: string | null;
}

export const storesApi = {
  list: () => api<{ stores: Store[] }>('/stores'),
  create: (name: string) => api<{ store: Store }>('/stores', 'POST', { name }),
  retry: (name: string) => api<{ store: Store }>(`/stores/${encodeURIComponent(name)}/retry`, 'POST'),
  remove: (name: string) => api<{ store: Store }>(`/stores/${encodeURIComponent(name)}`, 'DELETE'),
};

// ---- hosted MCP servers (SO-5, DESIGN §14) ----
export type DeployStatus = 'deploying' | 'deployed' | 'failed' | 'removing';

export interface HostedServer {
  serverId: string;
  slug: string;
  name: string;
  image: string;
  port: number;
  path: string;
  url: string;
  env: Record<string, string>;
  secretEnvKeys: string[];
  size: 'small' | 'medium';
  allowInternet: boolean;
  authType: AuthType;
  authHeaderName: string | null;
  deployStatus: DeployStatus;
  deployReason: string | null;
  attempt: number;
  serverStatus: ServerStatus;
  toolCount: number;
  createdAt: string;
  deployedAt: string | null;
}

export interface HostedInput {
  slug: string;
  name: string;
  image: string;
  port: number;
  path: string;
  env: Record<string, string>;
  secretEnv: Record<string, string>;
  size: 'small' | 'medium';
  allowInternet: boolean;
  authType: AuthType;
  authHeaderName?: string;
}

export const hostedApi = {
  list: () => api<{ hosted: HostedServer[] }>('/hosted-servers'),
  create: (body: HostedInput) => api<{ hosted: HostedServer }>('/hosted-servers', 'POST', body),
  retry: (id: string) => api<{ hosted: HostedServer }>(`/hosted-servers/${id}/retry`, 'POST'),
  remove: (id: string) => api<{ hosted: HostedServer }>(`/hosted-servers/${id}`, 'DELETE'),
};

// ---- insights (SO-8, DESIGN §16) ----
export type InsightsWindow = '1h' | '24h' | '7d';

export interface Insights {
  window: InsightsWindow;
  calls: number;
  errorRate: number;
  servers: {
    serverSlug: string;
    calls: number;
    errors: number;
    errorRate: number;
    p50Ms: number;
    p95Ms: number;
    outcomes: Partial<Record<Outcome, number>>;
  }[];
  tools: { exposedName: string; calls: number; errorRate: number; p95Ms: number }[];
  failures: { serverSlug: string; outcome: Outcome; message: string; count: number; lastSeen: string }[];
}

export const insightsApi = {
  get: (window: InsightsWindow) => api<Insights>(`/insights?window=${window}`),
};
