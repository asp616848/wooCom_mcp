import { Plus } from 'lucide-react';
import { Link, useNavigate } from 'react-router';

import { useMe } from '@/auth';
import { EmptyState, ErrorState, formatTime, Loading, PageHeader, StatusBadge } from '@/components/common';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Table, Td, Th } from '@/components/ui/table';
import { useServers } from '@/pages/servers/hooks';

export function ServersPage() {
  const isAdmin = useMe().data?.role === 'admin';
  const servers = useServers();
  const navigate = useNavigate();

  return (
    <>
      <PageHeader
        title="MCP servers"
        description="Every enabled, connected server's tools are available through the one gateway URL."
        actions={
          isAdmin && (
            <Button onClick={() => navigate('/servers/new')}>
              <Plus className="size-4" /> Add server
            </Button>
          )
        }
      />
      {servers.isPending && <Loading />}
      {servers.isError && <ErrorState error={servers.error} />}
      {servers.data && servers.data.servers.length === 0 && (
        <EmptyState>No servers registered yet.{isAdmin && ' Add one to expose its tools.'}</EmptyState>
      )}
      {servers.data && servers.data.servers.length > 0 && (
        <Table>
          <thead>
            <tr>
              <Th>Server</Th>
              <Th>Status</Th>
              <Th>Tools</Th>
              <Th>Protocol</Th>
              {isAdmin && <Th>URL</Th>}
              <Th>Last check</Th>
            </tr>
          </thead>
          <tbody>
            {servers.data.servers.map((s) => (
              <tr key={s.id} className="hover:bg-zinc-50">
                <Td>
                  <Link to={`/servers/${s.id}`} className="font-medium text-brand hover:underline">
                    {s.name}
                  </Link>
                  <div className="text-xs text-zinc-500">
                    {s.slug} {s.source === 'seed' && <Badge className="ml-1">built-in</Badge>}
                  </div>
                </Td>
                <Td>
                  <StatusBadge status={s.status} error={s.lastError} />
                </Td>
                <Td>{s.toolCount}</Td>
                <Td className="text-zinc-500">{s.protocolEra ?? '—'}</Td>
                {isAdmin && <Td className="max-w-64 truncate font-mono text-xs text-zinc-500">{s.url}</Td>}
                <Td className="whitespace-nowrap text-zinc-500">{formatTime(s.lastCheckedAt)}</Td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
    </>
  );
}
