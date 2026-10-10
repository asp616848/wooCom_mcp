import { useMutation } from '@tanstack/react-query';
import { cloneElement, useId, useState, type FormEvent, type ReactElement } from 'react';

import { errorMessage } from '@/components/common';
import { Button } from '@/components/ui/button';
import { Input, Label } from '@/components/ui/input';
import { Select } from '@/components/ui/select';
import {
  ApiError,
  serversApi,
  type AuthType,
  type McpServer,
  type ServerInput,
  type TestResult,
} from '@/lib/api';

type CredentialMode = 'keep' | 'replace' | 'clear';

const SSRF_HINT =
  'Allowed: in-cluster services in mcp-upstreams, mcp-hosted or store-* namespaces, or public https URLs.';

/** API errors shown inline (DESIGN §8.4): SSRF blocks get a hint about which URLs are allowed. */
export function describeError(error: unknown): { code: string; message: string } {
  if (error instanceof ApiError) {
    const message = error.code === 'SSRF_BLOCKED' ? `${error.message}. ${SSRF_HINT}` : error.message;
    return { code: error.code, message };
  }
  return { code: 'ERROR', message: errorMessage(error) };
}

/**
 * Create/edit form. Credentials are write-only (DESIGN §11): the API never returns them, so editing shows
 * "•••• set" with Replace / Clear instead of a value.
 */
export function ServerForm({
  server,
  submitLabel,
  pending,
  error,
  onSubmit,
}: {
  server?: McpServer;
  submitLabel: string;
  pending: boolean;
  /** The last failed save, shown above the buttons. */
  error?: unknown;
  onSubmit: (body: ServerInput) => void;
}) {
  const editing = server !== undefined;
  const [slug, setSlug] = useState('');
  const [name, setName] = useState(server?.name ?? '');
  const [url, setUrl] = useState(server?.url ?? '');
  const [authType, setAuthType] = useState<AuthType>(server?.authType ?? 'bearer');
  const [headerName, setHeaderName] = useState(server?.authHeaderName ?? 'X-Api-Key');
  const [credential, setCredential] = useState('');
  const [credentialMode, setCredentialMode] = useState<CredentialMode>(
    server?.hasCredential ? 'keep' : 'replace',
  );
  const [timeoutMs, setTimeoutMs] = useState(String(server?.timeoutMs ?? 20000));
  const [result, setResult] = useState<TestResult | null>(null);

  const test = useMutation({
    mutationFn: () =>
      serversApi.test({
        url,
        authType,
        authHeaderName: authType === 'header' ? headerName : null,
        credential: authType === 'none' ? null : credential || null,
        timeoutMs: Number(timeoutMs),
      }),
    meta: { silent: true },
    onSuccess: setResult,
    onError: (failure) => {
      const { code, message } = describeError(failure);
      setResult({ ok: false, era: null, toolCount: null, errorCode: code, error: message });
    },
  });

  function submit(event: FormEvent) {
    event.preventDefault();
    const body: ServerInput = {
      name,
      url,
      authType,
      authHeaderName: authType === 'header' ? headerName : null,
      timeoutMs: Number(timeoutMs),
    };
    if (!editing) body.slug = slug;
    if (authType !== 'none') {
      if (credentialMode === 'replace') body.credential = credential;
      if (credentialMode === 'clear') body.credential = null;
    }
    onSubmit(body);
  }

  const canTest = url !== '' && (authType === 'none' || credential !== '');

  return (
    <form className="max-w-xl space-y-4" onSubmit={submit}>
      {!editing && (
        <Field
          label="Slug"
          hint="Prefix of every tool name (gst → gst__calculate_gst). Can't be changed later."
        >
          <Input
            required
            pattern="[a-z][a-z0-9\-]{1,29}"
            placeholder="my-server"
            value={slug}
            onChange={(e) => setSlug(e.target.value)}
          />
        </Field>
      )}
      <Field label="Name">
        <Input required maxLength={80} value={name} onChange={(e) => setName(e.target.value)} />
      </Field>
      <Field
        label="MCP endpoint URL"
        hint="In-cluster: http://<service>.<namespace>.svc.cluster.local:<port>/mcp. External servers: https only."
      >
        <Input
          required
          type="url"
          placeholder="https://example.com/mcp"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
        />
      </Field>
      <div className="grid grid-cols-2 gap-4">
        <Field label="Authentication">
          <Select
            className="w-full"
            value={authType}
            onChange={(e) => setAuthType(e.target.value as AuthType)}
          >
            <option value="bearer">Bearer token</option>
            <option value="header">Custom header</option>
            <option value="none">None</option>
          </Select>
        </Field>
        {authType === 'header' && (
          <Field label="Header name">
            <Input required value={headerName} onChange={(e) => setHeaderName(e.target.value)} />
          </Field>
        )}
      </div>
      {authType !== 'none' && (
        <Field label="Credential" hint="Stored encrypted. Nobody can read it back, including admins.">
          {credentialMode === 'replace' ? (
            <div className="flex gap-2">
              <Input
                aria-label="Credential"
                type="password"
                autoComplete="off"
                required
                value={credential}
                onChange={(e) => setCredential(e.target.value)}
              />
              {server?.hasCredential && (
                <Button type="button" variant="ghost" onClick={() => setCredentialMode('keep')}>
                  Cancel
                </Button>
              )}
            </div>
          ) : (
            <div className="flex items-center gap-2 text-sm">
              <span className="text-zinc-600">
                {credentialMode === 'keep' ? '•••• set' : 'Will be cleared'}
              </span>
              <Button type="button" variant="outline" size="sm" onClick={() => setCredentialMode('replace')}>
                Replace
              </Button>
              {credentialMode === 'keep' && (
                <Button type="button" variant="ghost" size="sm" onClick={() => setCredentialMode('clear')}>
                  Clear
                </Button>
              )}
            </div>
          )}
        </Field>
      )}
      <Field
        label="Timeout (ms)"
        hint="1000–120000. A slow call returns a gateway error; the server stays connected."
      >
        <Input
          type="number"
          min={1000}
          max={120000}
          step={500}
          value={timeoutMs}
          onChange={(e) => setTimeoutMs(e.target.value)}
        />
      </Field>

      {result && (
        <p role="status" className={result.ok ? 'text-sm text-emerald-700' : 'text-sm text-red-700'}>
          {result.ok
            ? `Connected: ${result.toolCount} tools, ${result.era} protocol.`
            : `Failed (${result.errorCode}): ${result.error}`}
        </p>
      )}
      {error !== undefined && error !== null && (
        <p role="alert" className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
          Not saved ({describeError(error).code}): {describeError(error).message}
        </p>
      )}
      <div className="flex gap-2">
        <Button type="submit" disabled={pending}>
          {pending ? 'Saving…' : submitLabel}
        </Button>
        <Button
          type="button"
          variant="outline"
          disabled={!canTest || test.isPending}
          title={canTest ? undefined : 'Enter the URL (and the credential) to test'}
          onClick={() => test.mutate()}
        >
          {test.isPending ? 'Testing…' : 'Test connection'}
        </Button>
      </div>
    </form>
  );
}

/** Label + control + hint; the label and hint are linked to the control for screen readers. */
function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: ReactElement<{ id?: string; 'aria-describedby'?: string }>;
}) {
  const id = useId();
  const control =
    children.type === 'div'
      ? children
      : cloneElement(children, { id, 'aria-describedby': hint ? `${id}-hint` : undefined });
  return (
    <div className="space-y-1.5">
      <Label htmlFor={id}>{label}</Label>
      {control}
      {hint && (
        <p id={`${id}-hint`} className="text-xs text-zinc-500">
          {hint}
        </p>
      )}
    </div>
  );
}
