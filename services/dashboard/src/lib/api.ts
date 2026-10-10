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
