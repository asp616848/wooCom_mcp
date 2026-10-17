import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Plus, RotateCw, Trash2 } from 'lucide-react';
import { useState, type FormEvent } from 'react';
import { Link, Navigate } from 'react-router';

import { useMe } from '@/auth';
import { EmptyState, ErrorState, formatTime, Loading, PageHeader, StatusBadge } from '@/components/common';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Dialog } from '@/components/ui/dialog';
import { Input, Label } from '@/components/ui/input';
import { Select } from '@/components/ui/select';
import { Table, Td, Th } from '@/components/ui/table';
import { toast } from '@/components/ui/toast';
import { hostedApi, type AuthType, type DeployStatus, type HostedInput, type HostedServer } from '@/lib/api';
import { imageProblem, parseEnv, slugProblem } from '@/lib/hosted';

const STATUS: Record<DeployStatus, { label: string; tone: 'green' | 'red' | 'gray' | 'amber' }> = {
  deploying: { label: 'Deploying', tone: 'amber' },
  deployed: { label: 'Deployed', tone: 'green' },
  failed: { label: 'Failed', tone: 'red' },
  removing: { label: 'Removing', tone: 'gray' },
};

const EMPTY = {
  slug: '',
  name: '',
  image: '',
  port: '8080',
  path: '/mcp',
  size: 'small' as 'small' | 'medium',
  authType: 'bearer' as AuthType,
  authHeaderName: 'X-Api-Key',
  allowInternet: false,
  env: '',
  secretEnv: '',
};

/** SO-5 (DESIGN §14): admins deploy MCP servers from a container image into mcp-hosted. Polls every 3 s. */
export function HostedPage() {
  const me = useMe().data;
  const queryClient = useQueryClient();
  const [creating, setCreating] = useState(false);
  const [deleting, setDeleting] = useState<HostedServer | null>(null);
  const hosted = useQuery({
    queryKey: ['hosted'],
    queryFn: hostedApi.list,
    refetchInterval: 3000,
    enabled: me?.role === 'admin',
  });
  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ['hosted'] });
    void queryClient.invalidateQueries({ queryKey: ['servers'] });
  };

  const retry = useMutation({
    mutationFn: (h: HostedServer) => hostedApi.retry(h.serverId),
    onSuccess: ({ hosted: h }) => {
      refresh();
      toast.success(`Retrying "${h.slug}" (attempt ${h.attempt})`);
    },
  });
  const remove = useMutation({
    mutationFn: (h: HostedServer) => hostedApi.remove(h.serverId),
    onSuccess: ({ hosted: h }) => {
      refresh();
      setDeleting(null);
      toast.success(`Removing "${h.slug}": its tools are hidden first, then the deployment is uninstalled`);
    },
  });

  if (me?.role !== 'admin') return <Navigate to="/servers" replace />;

  return (
    <>
      <PageHeader
        title="Hosted servers"
        description="MCP servers the platform runs for you from a container image, in the mcp-hosted namespace."
        actions={
          <Button onClick={() => setCreating(true)}>
            <Plus className="size-4" /> Deploy server
          </Button>
        }
      />
      {hosted.isPending && <Loading />}
      {hosted.isError && <ErrorState error={hosted.error} />}
      {hosted.data &&
        (hosted.data.hosted.length === 0 ? (
          <EmptyState>No hosted servers yet.</EmptyState>
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>Server</Th>
                <Th>Image</Th>
                <Th>Deploy</Th>
                <Th>Health</Th>
                <Th>Created</Th>
                <Th />
              </tr>
            </thead>
            <tbody>
              {hosted.data.hosted.map((h) => (
                <tr key={h.serverId} className="align-top">
                  <Td>
                    {h.deployStatus === 'deployed' ? (
                      <Link
                        to={`/servers/${h.serverId}`}
                        className="font-medium text-blue-700 hover:underline"
                      >
                        {h.name}
                      </Link>
                    ) : (
                      <span className="font-medium">{h.name}</span>
                    )}
                    <div className="font-mono text-xs text-zinc-500">{h.slug}__*</div>
                  </Td>
                  <Td>
                    <div className="font-mono text-xs break-all">{h.image}</div>
                    <div className="mt-1 text-xs text-zinc-500">
                      {h.size} · auth {h.authType}
                      {h.allowInternet ? ' · internet' : ''}
                      {h.secretEnvKeys.length > 0 && ` · secrets: ${h.secretEnvKeys.join(', ')}`}
                    </div>
                  </Td>
                  <Td>
                    <span className="inline-flex items-center gap-2">
                      <Badge tone={STATUS[h.deployStatus].tone}>
                        <span className="size-1.5 rounded-full bg-current" />
                        {STATUS[h.deployStatus].label}
                      </Badge>
                      {(h.deployStatus === 'deploying' || h.deployStatus === 'removing') &&
                        h.deployReason && <span className="text-xs text-zinc-500">{h.deployReason}</span>}
                    </span>
                    {h.deployStatus === 'failed' && h.deployReason && (
                      <pre className="mt-2 max-w-md overflow-x-auto rounded bg-red-50 p-2 text-xs whitespace-pre-wrap text-red-800">
                        {h.deployReason}
                      </pre>
                    )}
                  </Td>
                  <Td>
                    <StatusBadge status={h.serverStatus} />
                    {h.deployStatus === 'deployed' && (
                      <div className="mt-1 text-xs text-zinc-500">{h.toolCount} tools</div>
                    )}
                  </Td>
                  <Td className="whitespace-nowrap text-zinc-500">{formatTime(h.createdAt)}</Td>
                  <Td className="text-right whitespace-nowrap">
                    {h.deployStatus === 'failed' && !h.deployReason?.startsWith('removal failed') && (
                      <Button
                        variant="ghost"
                        size="sm"
                        disabled={retry.isPending}
                        onClick={() => retry.mutate(h)}
                      >
                        <RotateCw className="size-4" /> Retry
                      </Button>
                    )}
                    {h.deployStatus !== 'removing' && (
                      <Button
                        variant="ghost"
                        size="sm"
                        className="text-red-700"
                        onClick={() => setDeleting(h)}
                      >
                        <Trash2 className="size-4" /> Delete
                      </Button>
                    )}
                  </Td>
                </tr>
              ))}
            </tbody>
          </Table>
        ))}

      <Dialog open={creating} onClose={() => setCreating(false)} title="Deploy an MCP server">
        {creating && (
          <DeployForm
            onDone={(h) => {
              refresh();
              setCreating(false);
              toast.success(`"${h.slug}" is being deployed (usually under a minute)`);
            }}
          />
        )}
      </Dialog>

      <Dialog
        open={deleting !== null}
        onClose={() => setDeleting(null)}
        title={`Delete hosted server "${deleting?.slug}"?`}
      >
        <p className="text-sm text-zinc-600">
          Its tools disappear for everyone right away; then its deployment, service and secrets are removed.
          The audit history is kept.
        </p>
        <div className="mt-4 flex justify-end gap-2">
          <Button variant="outline" onClick={() => setDeleting(null)}>
            Cancel
          </Button>
          <Button
            className="bg-red-700 hover:bg-red-800"
            disabled={remove.isPending}
            onClick={() => deleting && remove.mutate(deleting)}
          >
            {remove.isPending ? 'Deleting…' : 'Delete server'}
          </Button>
        </div>
      </Dialog>
    </>
  );
}

function DeployForm({ onDone }: { onDone: (h: HostedServer) => void }) {
  const [form, setForm] = useState(EMPTY);
  const set = <K extends keyof typeof EMPTY>(key: K, value: (typeof EMPTY)[K]) =>
    setForm((f) => ({ ...f, [key]: value }));
  const create = useMutation({
    mutationFn: (body: HostedInput) => hostedApi.create(body),
    meta: { silent: true }, // shown inline
    onSuccess: ({ hosted }) => onDone(hosted),
  });

  const env = parseEnv(form.env);
  const secretEnv = parseEnv(form.secretEnv);
  const port = Number(form.port);
  const problems = [
    slugProblem(form.slug),
    imageProblem(form.image),
    Number.isInteger(port) && port >= 1024 && port <= 65535 ? null : 'Port: 1024–65535.',
    env.problem && `Env: ${env.problem}`,
    secretEnv.problem && `Secret env: ${secretEnv.problem}`,
  ].filter(Boolean);
  const complete = form.slug && form.name.trim() && form.image && problems.length === 0;

  function submit(event: FormEvent) {
    event.preventDefault();
    if (!complete) return;
    create.mutate({
      slug: form.slug,
      name: form.name.trim(),
      image: form.image,
      port,
      path: form.path,
      env: env.env,
      secretEnv: secretEnv.env,
      size: form.size,
      allowInternet: form.allowInternet,
      authType: form.authType,
      ...(form.authType === 'header' ? { authHeaderName: form.authHeaderName } : {}),
    });
  }

  return (
    <form className="space-y-4" onSubmit={submit}>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Slug" id="h-slug" hint="Tools appear as <slug>__<tool>">
          <Input
            id="h-slug"
            required
            autoFocus
            maxLength={30}
            placeholder="pincode-hosted"
            value={form.slug}
            onChange={(e) => set('slug', e.target.value.trim().toLowerCase())}
          />
        </Field>
        <Field label="Name" id="h-name">
          <Input
            id="h-name"
            required
            maxLength={80}
            placeholder="Pincode (hosted)"
            value={form.name}
            onChange={(e) => set('name', e.target.value)}
          />
        </Field>
      </div>
      <Field label="Image" id="h-image" hint="wmg/, ghcr.io/ or docker.io/, with an explicit tag">
        <Input
          id="h-image"
          required
          className="font-mono"
          placeholder="ghcr.io/owner/my-mcp:1.2.0"
          value={form.image}
          onChange={(e) => set('image', e.target.value.trim())}
        />
      </Field>
      <div className="grid gap-4 sm:grid-cols-3">
        <Field label="Port" id="h-port">
          <Input
            id="h-port"
            inputMode="numeric"
            value={form.port}
            onChange={(e) => set('port', e.target.value)}
          />
        </Field>
        <Field label="Path" id="h-path">
          <Input id="h-path" value={form.path} onChange={(e) => set('path', e.target.value.trim())} />
        </Field>
        <Field label="Size" id="h-size">
          <Select
            id="h-size"
            className="w-full"
            value={form.size}
            onChange={(e) => set('size', e.target.value as 'small' | 'medium')}
          >
            <option value="small">small (≤ 200m CPU, 192Mi)</option>
            <option value="medium">medium (≤ 500m CPU, 320Mi)</option>
          </Select>
        </Field>
      </div>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Auth" id="h-auth" hint="The platform generates the credential and injects it">
          <Select
            id="h-auth"
            className="w-full"
            value={form.authType}
            onChange={(e) => set('authType', e.target.value as AuthType)}
          >
            <option value="bearer">Bearer token</option>
            <option value="header">Custom header</option>
            <option value="none">None</option>
          </Select>
        </Field>
        {form.authType === 'header' && (
          <Field label="Header name" id="h-header">
            <Input
              id="h-header"
              value={form.authHeaderName}
              onChange={(e) => set('authHeaderName', e.target.value.trim())}
            />
          </Field>
        )}
      </div>
      <Field label="Environment" id="h-env" hint="KEY=value per line">
        <textarea
          id="h-env"
          rows={3}
          className="w-full rounded-md border border-zinc-300 px-2 py-1.5 font-mono text-xs"
          value={form.env}
          onChange={(e) => set('env', e.target.value)}
        />
      </Field>
      <Field label="Secret environment" id="h-secret" hint="Stored encrypted; never shown again">
        <textarea
          id="h-secret"
          rows={2}
          className="w-full rounded-md border border-zinc-300 px-2 py-1.5 font-mono text-xs"
          value={form.secretEnv}
          onChange={(e) => set('secretEnv', e.target.value)}
        />
      </Field>
      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={form.allowInternet}
          onChange={(e) => set('allowInternet', e.target.checked)}
        />
        Allow outbound internet (HTTPS only; private networks stay blocked)
      </label>
      {problems.length > 0 && (
        <ul className="list-disc pl-5 text-xs text-red-700">
          {problems.map((p) => (
            <li key={p}>{p}</li>
          ))}
        </ul>
      )}
      {create.isError && <ErrorState error={create.error} />}
      <Button type="submit" disabled={create.isPending || !complete}>
        {create.isPending ? 'Deploying…' : 'Deploy'}
      </Button>
    </form>
  );
}

function Field({
  label,
  id,
  hint,
  children,
}: {
  label: string;
  id: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-1.5">
      <Label htmlFor={id}>{label}</Label>
      {children}
      {hint && <p className="text-xs text-zinc-500">{hint}</p>}
    </div>
  );
}
