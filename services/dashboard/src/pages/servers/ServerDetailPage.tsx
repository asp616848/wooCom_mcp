import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertTriangle, ArrowLeft, Pencil, Power, Trash2 } from 'lucide-react';
import { useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router';

import { useMe } from '@/auth';
import { EmptyState, ErrorState, formatTime, Loading, PageHeader, StatusBadge } from '@/components/common';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Dialog } from '@/components/ui/dialog';
import { Table, Td, Th } from '@/components/ui/table';
import { toast } from '@/components/ui/toast';
import { serversApi, type McpServer, type ServerInput } from '@/lib/api';
import { useServers } from '@/pages/servers/hooks';
import { ServerForm } from '@/pages/servers/ServerForm';

// DESIGN §8.7 rule 2: flag tool descriptions that look like prompt injection (admins only).
const SUSPICIOUS = /(ignore (all|previous)|system prompt|you must|<\/?(instructions|system)>|do not tell)/i;

export function ServerDetailPage() {
  const { id = '' } = useParams();
  const isAdmin = useMe().data?.role === 'admin';
  const servers = useServers();
  const server = servers.data?.servers.find((s) => s.id === id);
  const tools = useQuery({
    queryKey: ['servers', id, 'tools'],
    queryFn: () => serversApi.tools(id),
    enabled: server !== undefined,
  });

  if (servers.isPending) return <Loading />;
  if (servers.isError) return <ErrorState error={servers.error} />;
  if (!server) return <EmptyState>This server no longer exists.</EmptyState>;

  return (
    <>
      <Link
        to="/servers"
        className="mb-3 inline-flex items-center gap-1 text-sm text-zinc-500 hover:text-zinc-900"
      >
        <ArrowLeft className="size-4" /> All servers
      </Link>
      <PageHeader
        title={server.name}
        description={`${server.slug} · tools are exposed as ${server.slug}__<tool>`}
        actions={isAdmin && <AdminActions server={server} />}
      />
      <Card className="mb-6">
        <CardContent className="grid gap-4 pt-6 text-sm sm:grid-cols-4">
          <Info label="Status">
            <StatusBadge status={server.status} error={server.lastError} />
          </Info>
          <Info label="Protocol">{server.protocolEra ?? '—'}</Info>
          <Info label="Last check">{formatTime(server.lastCheckedAt)}</Info>
          <Info label="Source">{server.source}</Info>
          {isAdmin && (
            <>
              <Info label="URL" wide>
                <span className="font-mono text-xs break-all">{server.url}</span>
              </Info>
              <Info label="Auth">
                {server.authType}
                {server.authType === 'header' && ` (${server.authHeaderName})`}
                {server.authType !== 'none' && (server.hasCredential ? ' · •••• set' : ' · not set')}
              </Info>
              <Info label="Timeout">{server.timeoutMs} ms</Info>
              {server.lastError && (
                <Info label="Last error" wide>
                  <span className="text-red-700">
                    {server.lastErrorCode}: {server.lastError}
                  </span>
                </Info>
              )}
            </>
          )}
        </CardContent>
      </Card>

      <h2 className="mb-3 text-base font-semibold">Tools ({tools.data?.tools.length ?? server.toolCount})</h2>
      {tools.isPending && <Loading />}
      {tools.isError && <ErrorState error={tools.error} />}
      {tools.data && tools.data.tools.length === 0 && (
        <EmptyState>No tools yet. They appear after the first successful health check.</EmptyState>
      )}
      {tools.data && tools.data.tools.length > 0 && (
        <Table>
          <thead>
            <tr>
              <Th>Exposed name</Th>
              <Th>Description</Th>
              <Th>Access</Th>
            </tr>
          </thead>
          <tbody>
            {tools.data.tools.map((tool) => (
              <tr key={tool.exposedName}>
                <Td className="font-mono text-xs whitespace-nowrap">{tool.exposedName}</Td>
                <Td>
                  <p className="text-zinc-700">{tool.description ?? '—'}</p>
                  {isAdmin && tool.description && SUSPICIOUS.test(tool.description) && (
                    <Badge tone="amber" className="mt-1">
                      <AlertTriangle className="size-3" /> Description looks like instructions to the model
                    </Badge>
                  )}
                  <details className="mt-1">
                    <summary className="cursor-pointer text-xs text-zinc-500">Input schema</summary>
                    <pre className="mt-1 overflow-x-auto rounded bg-zinc-50 p-2 text-xs">
                      {JSON.stringify(tool.inputSchema, null, 2)}
                    </pre>
                  </details>
                </Td>
                <Td>{tool.adminOnly ? <Badge tone="brand">Admins only</Badge> : <Badge>Everyone</Badge>}</Td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
    </>
  );
}

function AdminActions({ server }: { server: McpServer }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const refresh = () => queryClient.invalidateQueries({ queryKey: ['servers'] });

  const update = useMutation({
    mutationFn: (body: ServerInput) => serversApi.update(server.id, body),
    meta: { silent: true }, // the form shows the error inline
    onSuccess: ({ server: s }) => {
      void refresh();
      setEditing(false);
      toast.success(`${s.name} saved (${s.status})`);
    },
  });
  const toggle = useMutation({
    mutationFn: () => serversApi.update(server.id, { enabled: !server.enabled }),
    onSuccess: ({ server: s }) => {
      void refresh();
      toast.success(s.enabled ? `${s.name} enabled` : `${s.name} disabled: its tools are hidden`);
    },
  });
  const remove = useMutation({
    mutationFn: () => serversApi.remove(server.id),
    onSuccess: () => {
      void refresh();
      toast.success(`${server.name} deleted`);
      navigate('/servers');
    },
  });
  const managedElsewhere = server.source === 'provisioned' || server.source === 'hosted';

  return (
    <>
      <Button variant="outline" onClick={() => setEditing(true)}>
        <Pencil className="size-4" /> Edit
      </Button>
      <Button variant="outline" disabled={toggle.isPending} onClick={() => toggle.mutate()}>
        <Power className="size-4" /> {server.enabled ? 'Disable' : 'Enable'}
      </Button>
      <Button
        variant="outline"
        className="text-red-700"
        disabled={managedElsewhere}
        title={managedElsewhere ? 'Delete the store / hosted server instead' : undefined}
        onClick={() => setConfirmDelete(true)}
      >
        <Trash2 className="size-4" /> Delete
      </Button>

      <Dialog
        open={editing}
        onClose={() => {
          setEditing(false);
          update.reset();
        }}
        title={`Edit ${server.name}`}
      >
        <ServerForm
          server={server}
          submitLabel="Save changes"
          pending={update.isPending}
          error={update.error}
          onSubmit={(body) => update.mutate(body)}
        />
      </Dialog>
      <Dialog open={confirmDelete} onClose={() => setConfirmDelete(false)} title={`Delete ${server.name}?`}>
        <p className="mb-4 text-sm text-zinc-600">
          Its {server.toolCount} tools disappear for everyone right away. The audit history is kept.
        </p>
        <div className="flex justify-end gap-2">
          <Button variant="outline" onClick={() => setConfirmDelete(false)}>
            Cancel
          </Button>
          <Button
            className="bg-red-600 hover:bg-red-700"
            disabled={remove.isPending}
            onClick={() => remove.mutate()}
          >
            Delete server
          </Button>
        </div>
      </Dialog>
    </>
  );
}

function Info({ label, wide, children }: { label: string; wide?: boolean; children: React.ReactNode }) {
  return (
    <div className={wide ? 'sm:col-span-2' : undefined}>
      <div className="text-xs text-zinc-500">{label}</div>
      <div className="mt-0.5">{children}</div>
    </div>
  );
}
