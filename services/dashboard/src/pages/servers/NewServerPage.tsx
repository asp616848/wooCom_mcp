import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Navigate, useNavigate } from 'react-router';

import { useMe } from '@/auth';
import { PageHeader } from '@/components/common';
import { Card, CardContent } from '@/components/ui/card';
import { toast } from '@/components/ui/toast';
import { serversApi, type ServerInput } from '@/lib/api';
import { ServerForm } from '@/pages/servers/ServerForm';

export function NewServerPage() {
  const me = useMe();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const create = useMutation({
    mutationFn: (body: ServerInput) => serversApi.create(body),
    meta: { silent: true }, // the form shows the error inline
    onSuccess: ({ server }) => {
      void queryClient.invalidateQueries({ queryKey: ['servers'] });
      if (server.status === 'connected') toast.success(`${server.name} added with ${server.toolCount} tools`);
      else
        toast.error(`${server.name} saved but ${server.status}: ${server.lastError ?? 'not reachable yet'}`);
      navigate(`/servers/${server.id}`);
    },
  });

  if (me.data?.role !== 'admin') return <Navigate to="/servers" replace />;
  return (
    <>
      <PageHeader
        title="Add MCP server"
        description="It is saved even if unreachable; the status shows why."
      />
      <Card>
        <CardContent className="pt-6">
          <ServerForm
            submitLabel="Add server"
            pending={create.isPending}
            error={create.error}
            onSubmit={(body) => create.mutate(body)}
          />
        </CardContent>
      </Card>
    </>
  );
}
